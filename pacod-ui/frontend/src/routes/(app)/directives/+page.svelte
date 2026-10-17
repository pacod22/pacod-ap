<script lang="ts">
	import { DEFAULT_PROFILE_ID } from '$lib/features/profiles/queries';
	import { Plus, Trash2, Pencil, X, Check } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		createDirectiveListQuery,
		createDirectiveMutation,
		updateDirectiveMutation,
		deleteDirectiveMutation,
		type Directive,
		type DirectiveCreateInput
	} from '$lib/features/directives/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { m } from '$lib/paraglide/messages';

	const directives = $derived(
		createDirectiveListQuery({ limit: 200, offset: 0, profile_id: selectedProfile.id ?? undefined })
	);
	const rows = $derived(
		[...($directives.data ?? [])].sort((a, b) => a.priority - b.priority || a.id - b.id)
	);

	const createMut = createDirectiveMutation();
	const updateMut = updateDirectiveMutation();
	const deleteMut = deleteDirectiveMutation();

	let dialog = $state<HTMLDialogElement | null>(null);
	let editingId = $state<number | null>(null);
	let formError = $state('');
	let confirmDeleteId = $state<number | null>(null);

	function blankForm(): DirectiveCreateInput {
		return {
			title: '',
			content: '',
			language: 'all',
			legal_year: null,
			priority: 100,
			is_active: true
		};
	}
	let form = $state<DirectiveCreateInput>(blankForm());

	function openCreate() {
		editingId = null;
		form = blankForm();
		formError = '';
		dialog?.showModal();
	}

	function openEdit(d: Directive) {
		editingId = d.id;
		form = {
			title: d.title,
			content: d.content,
			language: (d.language as 'bg' | 'en' | 'all') ?? 'all',
			legal_year: d.legal_year,
			priority: d.priority,
			is_active: d.is_active
		};
		formError = '';
		dialog?.showModal();
	}

	function closeDialog() {
		dialog?.close();
		formError = '';
	}

	async function submitForm() {
		formError = '';
		const payload: DirectiveCreateInput = {
			...form,
			legal_year:
				form.legal_year === null || String(form.legal_year) === '' ? null : Number(form.legal_year),
			priority: Number(form.priority)
		};
		try {
			if (editingId !== null) {
				await $updateMut.mutateAsync({ id: editingId, patch: payload });
			} else {
				await $createMut.mutateAsync({
					...payload,
					profile_id: selectedProfile.id ?? DEFAULT_PROFILE_ID
				});
			}
			closeDialog();
		} catch (e: unknown) {
			formError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	async function toggleActive(d: Directive) {
		try {
			await $updateMut.mutateAsync({ id: d.id, patch: { is_active: !d.is_active } });
		} catch {
			// invalidation handled in mutation
		}
	}

	async function confirmDelete(id: number) {
		try {
			await $deleteMut.mutateAsync(id);
			confirmDeleteId = null;
		} catch {
			// noop
		}
	}
</script>

<svelte:head><title>{m.directives_title()}</title></svelte:head>

<div class="space-y-6">
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
		<div>
			<h1 class="text-2xl font-semibold tracking-tight">{m.nav_directives()}</h1>
			<p class="mt-1 max-w-2xl text-sm text-[var(--color-text-muted)]">
				{m.directives_intro()}
			</p>
		</div>
		<Button size="sm" onclick={openCreate} class="w-full sm:w-auto">
			<Plus size={14} />
			{m.directives_new()}
		</Button>
	</div>

	<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.directives_col_priority()}</th>
						<th>{m.directives_col_title()}</th>
						<th>{m.directives_col_content()}</th>
						<th>{m.directives_col_lang()}</th>
						<th>{m.directives_col_year()}</th>
						<th>{m.directives_col_active()}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#if $directives.isPending}
						<tr
							><td colspan="7" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
								>{m.directives_loading()}</td
							></tr
						>
					{:else if $directives.isError}
						<tr
							><td colspan="7" class="py-12 text-center text-sm" style="color: var(--color-danger);"
								>{m.directives_load_failed()}</td
							></tr
						>
					{:else if rows.length > 0}
						{#each rows as d (d.id)}
							<tr>
								<td data-label={m.directives_col_priority()} class="tabular-nums">{d.priority}</td>
								<td data-label={m.directives_col_title()} class="font-medium">{d.title}</td>
								<td
									data-label={m.directives_col_content()}
									class="stack-block max-w-[360px] truncate text-[var(--color-text-muted)]"
									title={d.content}>{d.content}</td
								>
								<td
									data-label={m.directives_col_lang()}
									class="font-mono text-xs text-[var(--color-text-muted)] uppercase"
									>{d.language}</td
								>
								<td
									data-label={m.directives_col_year()}
									class="text-[var(--color-text-muted)] tabular-nums">{d.legal_year ?? '—'}</td
								>
								<td data-label={m.directives_col_active()}>
									<button
										onclick={() => toggleActive(d)}
										class="rounded-md px-1.5 py-0.5 text-xs font-medium"
										style={d.is_active
											? 'background: var(--color-brand-soft); color: var(--color-brand);'
											: 'background: var(--color-surface-3); color: var(--color-text-muted);'}
										title={m.common_toggle_active()}
									>
										{d.is_active ? m.common_active_lc() : m.common_inactive_lc()}
									</button>
								</td>
								<td data-label="" class="w-20">
									{#if confirmDeleteId === d.id}
										<div class="flex items-center justify-end gap-1">
											<button
												onclick={() => confirmDelete(d.id)}
												class="touch-target rounded-lg p-1.5"
												style="color: var(--color-danger);"
												aria-label={m.common_confirm_delete()}
												title={m.common_confirm_delete()}><Check size={14} /></button
											>
											<button
												onclick={() => (confirmDeleteId = null)}
												class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-3)]"
												aria-label={m.common_cancel()}
												title={m.common_cancel()}><X size={14} /></button
											>
										</div>
									{:else}
										<div class="flex items-center justify-end gap-1">
											<button
												onclick={() => openEdit(d)}
												class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-text)]"
												aria-label={m.common_edit()}
												title={m.common_edit()}><Pencil size={14} /></button
											>
											<button
												onclick={() => (confirmDeleteId = d.id)}
												class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-danger)]"
												aria-label={m.common_delete()}
												title={m.common_delete()}><Trash2 size={14} /></button
											>
										</div>
									{/if}
								</td>
							</tr>
						{/each}
					{:else}
						<tr
							><td colspan="7" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
								>{m.directives_empty()}</td
							></tr
						>
					{/if}
				</tbody>
			</table>
		</div>
	</div>
</div>

<!-- Create / Edit dialog -->
<dialog
	bind:this={dialog}
	class="mx-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-xl overflow-y-auto rounded-2xl p-0 shadow-2xl"
	style="background: var(--color-surface); border: 1px solid var(--color-border);"
>
	<form
		onsubmit={(e) => {
			e.preventDefault();
			submitForm();
		}}
	>
		<div
			class="flex items-center justify-between border-b px-6 py-4"
			style="border-color: var(--color-border);"
		>
			<h2 class="text-base font-semibold">
				{editingId ? m.directives_edit_title() : m.directives_new_title()}
			</h2>
			<button
				type="button"
				onclick={closeDialog}
				class="rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
				aria-label={m.common_close()}><X size={16} /></button
			>
		</div>

		<div class="space-y-4 px-6 py-5">
			<div>
				<label class="field-label" for="fld-page-directives-1">{m.directives_field_title()}</label>
				<input
					id="fld-page-directives-1"
					type="text"
					bind:value={form.title}
					required
					maxlength="255"
					placeholder={m.directives_field_title_ph()}
					class="field-input"
				/>
			</div>
			<div>
				<label class="field-label" for="fld-page-directives-2">{m.directives_field_content()}</label
				>
				<textarea
					id="fld-page-directives-2"
					bind:value={form.content}
					required
					rows={5}
					placeholder={m.directives_field_content_ph()}
					class="field-textarea"></textarea>
			</div>
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
				<div>
					<label class="field-label" for="fld-page-directives-3"
						>{m.directives_field_language()}</label
					>
					<select id="fld-page-directives-3" bind:value={form.language} class="field-input">
						<option value="all">all</option>
						<option value="bg">bg</option>
						<option value="en">en</option>
					</select>
				</div>
				<div>
					<label class="field-label" for="fld-page-directives-4"
						>{m.directives_field_legal_year()}</label
					>
					<input
						id="fld-page-directives-4"
						type="number"
						bind:value={form.legal_year}
						placeholder={m.directives_field_legal_year_ph()}
						class="field-input"
					/>
				</div>
				<div>
					<label class="field-label" for="fld-page-directives-5"
						>{m.directives_field_priority()}</label
					>
					<input
						id="fld-page-directives-5"
						type="number"
						bind:value={form.priority}
						class="field-input"
					/>
				</div>
			</div>
			<label class="flex items-center gap-2 text-sm">
				<input type="checkbox" bind:checked={form.is_active} />
				{m.directives_field_active()}
			</label>
		</div>

		{#if formError}
			<div
				class="mx-6 mb-2 rounded-lg px-3 py-2 text-sm"
				style="background: var(--color-danger-soft); color: var(--color-danger);"
			>
				{formError}
			</div>
		{/if}

		<div
			class="flex justify-end gap-2 border-t px-6 py-4"
			style="border-color: var(--color-border);"
		>
			<Button type="button" variant="secondary" size="sm" onclick={closeDialog}
				>{m.common_cancel()}</Button
			>
			<Button type="submit" size="sm" disabled={$createMut.isPending || $updateMut.isPending}>
				{editingId ? m.common_save_changes() : m.common_create()}
			</Button>
		</div>
	</form>
</dialog>
