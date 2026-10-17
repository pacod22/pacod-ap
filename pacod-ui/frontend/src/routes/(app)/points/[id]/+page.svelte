<script lang="ts">
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { ArrowLeft, Trash2, Pencil, X } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		createPointQuery,
		deletePointMutation,
		updatePointMutation,
		type PointUpdateInput
	} from '$lib/features/knowledge/queries';
	import { hasRole } from '$lib/types/auth';
	import { m } from '$lib/paraglide/messages';

	let { data } = $props();

	const id = $derived(page.params.id ?? '');
	const pid = $derived(selectedProfile.id);
	const point = $derived(createPointQuery(id, pid));
	const deleteMut = $derived(deletePointMutation(pid));
	const updateMut = $derived(updatePointMutation(pid));

	const isAdmin = $derived(hasRole(data.user, 'admin'));

	async function handleDelete() {
		if (!confirm(m.point_delete_confirm())) return;
		await $deleteMut.mutateAsync(id);
		goto('/points');
	}

	// ---- Edit ------------------------------------------------------
	let editing = $state(false);
	let editText = $state('');
	let editTitle = $state('');
	let editSourceType = $state('');
	let editLanguage = $state('');
	let editYear = $state('');
	let editUrl = $state('');
	let editMessage = $state('');
	let editError = $state('');

	function str(v: unknown): string {
		return v === null || v === undefined ? '' : String(v);
	}

	function startEdit() {
		const p = $point.data?.payload ?? {};
		editText = str(p.text);
		editTitle = str(p.title);
		editSourceType = str(p.source_type);
		editLanguage = str(p.language);
		editYear = str(p.legal_year);
		editUrl = str(p.url);
		editMessage = '';
		editError = '';
		editing = true;
	}

	async function handleSave() {
		editMessage = '';
		editError = '';
		const p = $point.data?.payload ?? {};
		const patch: PointUpdateInput = {};
		// Only send changed fields. Sending `text` triggers a re-embed server-side.
		if (editText.trim() !== str(p.text)) patch.text = editText.trim();
		if (editTitle !== str(p.title)) patch.title = editTitle;
		if (editSourceType !== str(p.source_type)) patch.source_type = editSourceType;
		if (editLanguage !== str(p.language)) patch.language = editLanguage;
		if (editUrl !== str(p.url)) patch.url = editUrl.trim() || null;
		if (editYear !== str(p.legal_year)) patch.legal_year = editYear ? Number(editYear) : null;

		if (Object.keys(patch).length === 0) {
			editError = m.point_no_changes();
			return;
		}
		try {
			await $updateMut.mutateAsync({ id, patch });
			editMessage = patch.text ? m.point_saved_reembed() : m.point_saved();
			editing = false;
		} catch (e) {
			editError = e instanceof Error ? e.message : m.point_save_failed();
		}
	}

	function formatValue(v: unknown): string {
		if (v === null || v === undefined) return '—';
		if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v);
		return JSON.stringify(v, null, 2);
	}

	function isComplex(v: unknown): boolean {
		return v !== null && typeof v === 'object';
	}
</script>

<svelte:head><title>{m.point_detail_title()}</title></svelte:head>

<div class="space-y-6">
	<!-- Back + header -->
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
		<div class="flex items-center gap-3">
			<a
				href="/points"
				class="rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-3)] hover:text-[var(--color-text)]"
				aria-label={m.point_back()}
			>
				<ArrowLeft size={18} />
			</a>
			<div>
				<h1 class="text-2xl font-semibold tracking-tight">{m.point_heading()}</h1>
				<p class="mt-0.5 font-mono text-xs text-[var(--color-text-muted)]">{id}</p>
			</div>
		</div>
		{#if isAdmin}
			<div class="flex items-center gap-2 self-end sm:self-auto">
				{#if editing}
					<Button variant="secondary" size="sm" onclick={() => (editing = false)}>
						<X size={14} />
						{m.common_cancel()}
					</Button>
				{:else}
					<Button variant="secondary" size="sm" onclick={startEdit} disabled={!$point.data}>
						<Pencil size={14} />
						{m.common_edit()}
					</Button>
				{/if}
				<Button variant="danger" size="sm" onclick={handleDelete} disabled={$deleteMut.isPending}>
					<Trash2 size={14} />
					{$deleteMut.isPending ? m.point_deleting() : m.point_delete_btn()}
				</Button>
			</div>
		{/if}
	</div>

	{#if editMessage}
		<div
			class="rounded-lg px-3 py-2 text-xs"
			style="background: var(--color-success-soft); color: var(--color-success);"
		>
			{editMessage}
		</div>
	{/if}

	{#if $point.isPending}
		<div
			class="flex h-40 items-center justify-center rounded-xl border text-sm text-[var(--color-text-muted)]"
			style="border-color: var(--color-border); background: var(--color-surface-2);"
		>
			{m.common_loading()}
		</div>
	{:else if $point.isError}
		<div
			class="rounded-xl border px-5 py-4 text-sm"
			style="border-color: var(--color-danger); background: var(--color-danger-soft); color: var(--color-danger);"
		>
			{m.point_load_failed()}
		</div>
	{:else if $point.data && editing}
		<!-- Edit form -->
		<div
			class="rounded-xl border p-5"
			style="border-color: var(--color-border); background: var(--color-surface-2);"
		>
			<p class="mb-4 text-xs text-[var(--color-text-muted)]">
				{m.point_edit_hint()}
			</p>
			<div class="space-y-3">
				<div>
					<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]"
						>{m.point_field_text()}</span
					>
					<textarea bind:value={editText} rows="5" class="field-input w-full"></textarea>
				</div>
				<div class="grid gap-3 sm:grid-cols-2">
					<div>
						<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]"
							>{m.point_field_title()}</span
						>
						<input type="text" bind:value={editTitle} class="field-input w-full" />
					</div>
					<div>
						<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]"
							>{m.ingest_col_source_type()}</span
						>
						<input type="text" bind:value={editSourceType} class="field-input w-full" />
					</div>
					<div>
						<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]"
							>{m.directives_field_language()}</span
						>
						<select bind:value={editLanguage} class="field-input w-full">
							<option value="bg">bg</option>
							<option value="en">en</option>
						</select>
					</div>
					<div>
						<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]"
							>{m.directives_field_legal_year()}</span
						>
						<input type="text" bind:value={editYear} class="field-input w-full" />
					</div>
					<div class="sm:col-span-2">
						<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]">URL</span>
						<input type="text" bind:value={editUrl} class="field-input w-full" />
					</div>
				</div>

				{#if editError}
					<div
						class="rounded-lg px-3 py-2 text-xs"
						style="background: var(--color-danger-soft); color: var(--color-danger);"
					>
						{editError}
					</div>
				{/if}

				<div class="flex items-center gap-3">
					<Button variant="primary" size="sm" onclick={handleSave} disabled={$updateMut.isPending}>
						{$updateMut.isPending ? m.point_saving() : m.common_save_changes()}
					</Button>
					<Button
						variant="secondary"
						size="sm"
						onclick={() => (editing = false)}
						disabled={$updateMut.isPending}
					>
						{m.common_cancel()}
					</Button>
				</div>
			</div>
		</div>
	{:else if $point.data}
		<!-- Payload table -->
		<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th class="w-48">{m.point_col_field()}</th>
						<th>{m.point_col_value()}</th>
					</tr>
				</thead>
				<tbody>
					{#each Object.entries($point.data.payload) as [key, value] (key)}
						<tr class={isComplex(value) ? 'align-top' : ''}>
							<td
								data-label=""
								class="stack-block font-mono text-xs font-medium text-[var(--color-text-muted)]"
								>{key}</td
							>
							<td data-label="" class="stack-block">
								{#if isComplex(value)}
									<pre
										class="overflow-x-auto rounded-lg p-3 text-xs"
										style="background: var(--color-surface-3); color: var(--color-text);">{formatValue(
											value
										)}</pre>
								{:else}
									<span class="text-sm">{formatValue(value)}</span>
								{/if}
							</td>
						</tr>
					{:else}
						<tr>
							<td colspan="2" class="py-8 text-center text-sm text-[var(--color-text-muted)]">
								{m.point_no_payload()}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
