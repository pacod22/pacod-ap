<script lang="ts">
	import { DEFAULT_PROFILE_ID, profilesQuery, shortNameFor } from '$lib/features/profiles/queries';
	import { Plus, Trash2, X, Check, Search, Pencil, Archive, ArrowRightLeft } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		createCuratedListQuery,
		createCuratedContentQuery,
		updateCuratedContentMutation,
		moveCuratedMutation,
		retireCuratedMutation,
		deleteCuratedMutation
	} from '$lib/features/curated/queries';
	import {
		ingestTextMutation,
		MANUAL_SOURCE_TYPES,
		type IngestionSource,
		type ManualSourceType
	} from '$lib/features/ingest/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { m } from '$lib/paraglide/messages';
	import { getLocale } from '$lib/paraglide/runtime';

	const STATUS_STYLES: Record<string, { bg: string; color: string }> = {
		DONE: { bg: 'var(--color-success-soft)', color: 'var(--color-success)' },
		OUTDATED: {
			bg: 'color-mix(in oklch, var(--color-warning, #f59e0b) 15%, transparent)',
			color: 'color-mix(in oklch, var(--color-warning, #f59e0b) 85%, var(--color-text))'
		}
	};

	function statusLabel(status: string): string {
		switch (status) {
			case 'DONE':
				return m.curated_status_active();
			case 'OUTDATED':
				return m.curated_status_retired();
			default:
				return status;
		}
	}

	function statusStyle(status: string) {
		return (
			STATUS_STYLES[status] ?? { bg: 'var(--color-surface-3)', color: 'var(--color-text-muted)' }
		);
	}

	function formatDate(iso: string | null | undefined): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleDateString();
	}

	// ---- Data -------------------------------------------------------
	const entries = $derived(createCuratedListQuery(selectedProfile.id));
	const updateMut = updateCuratedContentMutation();
	const createMut = ingestTextMutation();
	const retireMut = retireCuratedMutation();
	const deleteMut = deleteCuratedMutation();
	const moveMut = moveCuratedMutation();

	// Every active agent owns one curated collection, so each is a move target.
	const profiles = profilesQuery();
	const curatedProfiles = $derived(($profiles.data ?? []).filter((p) => p.is_active));
	const profileById = $derived(new Map(($profiles.data ?? []).map((p) => [p.id, p])));

	function entryProfileId(entry: { profile_id?: string | null }): string {
		return entry.profile_id ?? DEFAULT_PROFILE_ID;
	}

	function profileLabel(pid: string): string {
		const p = profileById.get(pid);
		return p ? shortNameFor(p, getLocale()) : pid;
	}

	function collectionName(pid: string): string {
		return profileById.get(pid)?.curated_collection ?? pid;
	}

	let filterTitle = $state('');
	const visibleEntries = $derived(
		($entries.data ?? []).filter((e) => {
			const tf = filterTitle.trim().toLowerCase();
			return !tf || e.title.toLowerCase().includes(tf) || e.source_id.toLowerCase().includes(tf);
		})
	);

	// ---- Editor -----------------------------------------------------
	// `editingId` drives both the dialog and the lazy content fetch: null means the
	// dialog is closed or we're creating, so nothing is fetched.
	let editingId = $state<number | null>(null);
	// The edited row's OWN profile. Saves and moves are keyed on it, never on the
	// scope selector — in global scope that is blank, and falling back to the
	// default profile would silently re-home another agent's entry on save.
	let editingProfileId = $state<string>(DEFAULT_PROFILE_ID);
	const content = $derived(createCuratedContentQuery(editingId));
	const editingCollection = $derived($content.data?.collection ?? collectionName(editingProfileId));
	const moveTargets = $derived(curatedProfiles.filter((p) => p.id !== editingProfileId));
	let moveTarget = $state('');
	let confirmMove = $state(false);

	type Form = {
		title: string;
		body: string;
		sourceType: ManualSourceType;
		language: '' | 'bg' | 'en';
		legalYear: string;
	};
	const emptyForm = (): Form => ({
		title: '',
		body: '',
		sourceType: 'curated_note',
		language: '',
		legalYear: ''
	});

	let dialog = $state<HTMLDialogElement | null>(null);
	let form = $state<Form>(emptyForm());
	let dialogError = $state('');
	// `movedTo` set → the success panel reports a relocation instead of a save.
	let dialogSuccess = $state<{ chunks: number; title: string; movedTo?: string } | null>(null);
	// Set when the body had to be rebuilt from the stored chunks (no authored copy).
	let reconstructed = $state(false);
	// Guards the one-shot seeding of the form from the fetched content, so typing
	// isn't clobbered on every refetch.
	let seededFor = $state<number | null>(null);

	$effect(() => {
		const loaded = $content.data;
		if (!loaded || editingId === null || seededFor === editingId) return;
		form = {
			title: loaded.title,
			body: loaded.body,
			sourceType: (MANUAL_SOURCE_TYPES as readonly string[]).includes(loaded.source_type)
				? (loaded.source_type as ManualSourceType)
				: 'curated_note',
			language: loaded.language === 'en' ? 'en' : 'bg',
			legalYear: String(loaded.legal_year)
		};
		reconstructed = loaded.reconstructed;
		seededFor = editingId;
	});

	function resetMove() {
		moveTarget = '';
		confirmMove = false;
	}

	function openCreate() {
		editingId = null;
		editingProfileId = selectedProfile.id ?? DEFAULT_PROFILE_ID;
		resetMove();
		seededFor = null;
		form = emptyForm();
		reconstructed = false;
		dialogError = '';
		dialogSuccess = null;
		dialog?.showModal();
	}

	function openEdit(entry: IngestionSource) {
		editingId = entry.id;
		editingProfileId = entryProfileId(entry);
		resetMove();
		seededFor = null;
		// Seed what we already have so the dialog isn't blank while the body loads.
		form = {
			title: entry.title,
			body: '',
			sourceType: (MANUAL_SOURCE_TYPES as readonly string[]).includes(entry.source_type)
				? (entry.source_type as ManualSourceType)
				: 'curated_note',
			language: entry.language === 'en' ? 'en' : 'bg',
			legalYear: String(entry.legal_year)
		};
		reconstructed = false;
		dialogError = '';
		dialogSuccess = null;
		dialog?.showModal();
	}

	function closeDialog() {
		dialog?.close();
		editingId = null;
		seededFor = null;
		dialogError = '';
		dialogSuccess = null;
	}

	const isPending = $derived($updateMut.isPending || $createMut.isPending || $moveMut.isPending);

	async function save() {
		if (!form.title.trim() || !form.body.trim()) return;
		dialogError = '';
		const shared = {
			title: form.title.trim(),
			body: form.body,
			tier: 'curated' as const,
			sourceType: form.sourceType,
			language: form.language || undefined,
			legalYear: form.legalYear ? Number(form.legalYear) : undefined,
			profileId: editingProfileId
		};
		try {
			const res =
				editingId === null
					? await $createMut.mutateAsync(shared)
					: await $updateMut.mutateAsync({ id: editingId, ...shared });
			dialogSuccess = { chunks: res.chunks, title: res.source.title };
		} catch (e) {
			dialogError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	async function doMove() {
		if (editingId === null || !moveTarget) return;
		dialogError = '';
		try {
			const res = await $moveMut.mutateAsync({ id: editingId, profileId: moveTarget });
			// The row was re-created under the target profile with a new id; the
			// old id is gone, so the dialog must not keep pointing at it.
			editingId = null;
			seededFor = null;
			dialogSuccess = {
				chunks: res.chunks,
				title: res.source.title,
				movedTo: collectionName(moveTarget)
			};
			resetMove();
		} catch (e) {
			dialogError = e instanceof Error ? e.message : m.common_unexpected_error();
			confirmMove = false;
		}
	}

	// ---- Row actions -------------------------------------------------
	let confirmDeleteId = $state<number | null>(null);
	let confirmRetireId = $state<number | null>(null);

	async function doRetire(id: number) {
		try {
			await $retireMut.mutateAsync(id);
			confirmRetireId = null;
		} catch {
			// surfaced by the mutation's error state
		}
	}

	async function doDelete(id: number) {
		try {
			await $deleteMut.mutateAsync(id);
			confirmDeleteId = null;
		} catch {
			// surfaced by the mutation's error state
		}
	}
</script>

<svelte:head><title>{m.curated_title()}</title></svelte:head>

<div class="space-y-6">
	<!-- Header -->
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
		<div>
			<h1 class="text-2xl font-semibold tracking-tight">{m.nav_curated()}</h1>
			<p class="mt-1 max-w-2xl text-sm text-[var(--color-text-muted)]">
				{m.curated_intro()}
			</p>
		</div>
		<Button size="sm" onclick={openCreate} class="w-full sm:w-auto">
			<Plus size={14} />
			{m.curated_new_entry()}
		</Button>
	</div>

	<!-- Filter -->
	<div
		class="flex flex-col flex-wrap gap-2 rounded-xl border p-3 sm:flex-row sm:items-center"
		style="background: var(--color-surface-2); border-color: var(--color-border);"
	>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-brand)_18%,transparent)] sm:w-72"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.curated_filter_title()}
				bind:value={filterTitle}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-text-muted)] placeholder:opacity-70"
			/>
		</label>
		{#if filterTitle}
			<Button variant="ghost" size="sm" onclick={() => (filterTitle = '')}>
				<X size={14} />
				{m.common_clear()}
			</Button>
		{/if}
	</div>

	<!-- Table -->
	<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.common_status()}</th>
						<th>{m.directives_col_title()}</th>
						<th>{m.curated_col_collection()}</th>
						<th>{m.ingest_col_source_type()}</th>
						<th>{m.common_reference_year()}</th>
						<th>{m.common_lang()}</th>
						<th>{m.ingest_col_chunks()}</th>
						<th>{m.ingest_col_ingested()}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#if $entries.isPending}
						<tr>
							<td colspan="9" class="py-12 text-center text-sm text-[var(--color-text-muted)]">
								{m.common_loading()}
							</td>
						</tr>
					{:else if $entries.isError}
						<tr>
							<td colspan="9" class="py-12 text-center text-sm" style="color: var(--color-danger);">
								{m.curated_load_failed()}
							</td>
						</tr>
					{:else if visibleEntries.length > 0}
						{#each visibleEntries as entry (entry.id)}
							{@const st = statusStyle(entry.status)}
							{@const pid = entryProfileId(entry)}
							<tr>
								<td data-label={m.common_status()} class="w-24">
									<span
										class="rounded-md px-2 py-0.5 text-xs font-medium"
										style="background: {st.bg}; color: {st.color};"
									>
										{statusLabel(entry.status)}
									</span>
								</td>
								<td data-label={m.directives_col_title()} class="stack-block max-w-xs">
									<span class="line-clamp-2 text-sm font-medium" title={entry.title}>
										{entry.title}
									</span>
									<span class="mt-0.5 block font-mono text-[11px] text-[var(--color-text-muted)]">
										{entry.source_id}
									</span>
								</td>
								<td data-label={m.curated_col_collection()}>
									<span class="block text-sm">{profileLabel(pid)}</span>
									<span class="mt-0.5 block font-mono text-[11px] text-[var(--color-text-muted)]">
										{collectionName(pid)}
									</span>
								</td>
								<td data-label={m.ingest_col_source_type()}>
									<span
										class="rounded-md px-2 py-0.5 text-xs font-medium"
										style="background: var(--color-surface-3); color: var(--color-text-muted);"
									>
										{entry.source_type}
									</span>
								</td>
								<td data-label={m.common_reference_year()} class="tabular-nums"
									>{entry.legal_year}</td
								>
								<td data-label={m.common_lang()}>{entry.language}</td>
								<td data-label={m.ingest_col_chunks()} class="tabular-nums">{entry.chunk_count}</td>
								<td data-label={m.ingest_col_ingested()} class="text-sm whitespace-nowrap">
									{formatDate(entry.ingested_at)}
								</td>
								<td class="w-px">
									<div class="flex items-center justify-end gap-1">
										{#if confirmDeleteId === entry.id}
											<button
												type="button"
												onclick={() => doDelete(entry.id)}
												class="icon-btn"
												aria-label={m.common_confirm_delete()}
												style="color: var(--color-danger);"
											>
												<Check size={14} />
											</button>
											<button
												type="button"
												onclick={() => (confirmDeleteId = null)}
												class="icon-btn"
												aria-label={m.common_cancel()}
											>
												<X size={14} />
											</button>
										{:else if confirmRetireId === entry.id}
											<button
												type="button"
												onclick={() => doRetire(entry.id)}
												class="icon-btn"
												aria-label={m.curated_confirm_retire()}
												style="color: var(--color-warning, #f59e0b);"
											>
												<Check size={14} />
											</button>
											<button
												type="button"
												onclick={() => (confirmRetireId = null)}
												class="icon-btn"
												aria-label={m.common_cancel()}
											>
												<X size={14} />
											</button>
										{:else}
											<button
												type="button"
												onclick={() => openEdit(entry)}
												class="icon-btn"
												aria-label={m.curated_edit()}
												title={m.curated_edit()}
											>
												<Pencil size={14} />
											</button>
											{#if entry.status !== 'OUTDATED'}
												<button
													type="button"
													onclick={() => {
														confirmRetireId = entry.id;
														confirmDeleteId = null;
													}}
													class="icon-btn"
													aria-label={m.curated_retire()}
													title={m.curated_retire_hint()}
												>
													<Archive size={14} />
												</button>
											{/if}
											<button
												type="button"
												onclick={() => {
													confirmDeleteId = entry.id;
													confirmRetireId = null;
												}}
												class="icon-btn"
												aria-label={m.common_delete()}
												title={m.common_delete()}
											>
												<Trash2 size={14} />
											</button>
										{/if}
									</div>
								</td>
							</tr>
						{/each}
					{:else}
						<tr>
							<td colspan="9" class="py-12 text-center text-sm text-[var(--color-text-muted)]">
								{m.curated_empty()}
							</td>
						</tr>
					{/if}
				</tbody>
			</table>
		</div>
	</div>
</div>

<!-- Editor dialog -->
<dialog
	bind:this={dialog}
	class="w-[min(48rem,92vw)] rounded-2xl border p-0 backdrop:bg-black/40"
	style="background: var(--color-surface); border-color: var(--color-border); color: var(--color-text);"
	onclose={() => {
		editingId = null;
		seededFor = null;
	}}
>
	{#if dialogSuccess}
		<div class="px-6 py-8 text-center">
			<p class="text-sm font-medium">{dialogSuccess.title}</p>
			<p class="mt-2 text-sm text-[var(--color-text-muted)]">
				{#if dialogSuccess.movedTo}
					{m.curated_moved({ collection: dialogSuccess.movedTo })}
				{:else}
					{m.curated_saved({ n: dialogSuccess.chunks })}
				{/if}
			</p>
			<div class="mt-6 flex justify-center">
				<Button size="sm" onclick={closeDialog}>{m.ingest_done()}</Button>
			</div>
		</div>
	{:else}
		<div class="border-b px-6 py-4" style="border-color: var(--color-border);">
			<h2 class="text-base font-semibold">
				{editingId === null ? m.curated_new_entry() : m.curated_edit()}
			</h2>
		</div>

		<form
			onsubmit={(e) => {
				e.preventDefault();
				save();
			}}
		>
			<div class="space-y-4 px-6 py-5">
				{#if editingId !== null && $content.isPending}
					<p class="text-sm text-[var(--color-text-muted)]">{m.common_loading()}</p>
				{/if}
				{#if editingId !== null && $content.isError}
					<div
						class="rounded-lg px-3 py-2 text-sm"
						style="background: var(--color-danger-soft); color: var(--color-danger);"
					>
						{m.curated_content_load_failed()}
					</div>
				{/if}
				{#if reconstructed}
					<div
						class="rounded-lg border px-3 py-2.5 text-xs leading-relaxed"
						style="background: var(--color-surface-2); border-color: var(--color-border); color: var(--color-text-muted);"
					>
						{m.curated_reconstructed_note()}
					</div>
				{/if}

				<div
					class="rounded-lg border px-3 py-2.5"
					style="background: var(--color-surface-2); border-color: var(--color-border);"
				>
					<p class="field-label">{m.curated_col_collection()}</p>
					<p class="text-sm">
						{profileLabel(editingProfileId)}
						<span class="ml-1 font-mono text-xs text-[var(--color-text-muted)]">
							{editingCollection}
						</span>
					</p>

					{#if editingId !== null}
						<div class="mt-3 border-t pt-3" style="border-color: var(--color-border);">
							<label class="field-label" for="curated-move-target">
								{m.curated_move_title()}
							</label>
							{#if moveTargets.length === 0}
								<p class="text-xs text-[var(--color-text-muted)]">{m.curated_move_none()}</p>
							{:else}
								<div class="flex flex-col gap-2 sm:flex-row sm:items-center">
									<select
										id="curated-move-target"
										bind:value={moveTarget}
										onchange={() => (confirmMove = false)}
										disabled={isPending}
										class="field-input sm:flex-1"
									>
										<option value="">{m.curated_move_placeholder()}</option>
										{#each moveTargets as p (p.id)}
											<option value={p.id}>
												{shortNameFor(p, getLocale())} — {p.curated_collection}
											</option>
										{/each}
									</select>
									{#if confirmMove}
										<div class="flex gap-2">
											<Button
												type="button"
												size="sm"
												onclick={doMove}
												disabled={isPending || !$content.isSuccess}
											>
												<Check size={14} />
												{m.curated_move_confirm()}
											</Button>
											<Button
												type="button"
												variant="ghost"
												size="sm"
												onclick={() => (confirmMove = false)}
												disabled={isPending}
											>
												<X size={14} />
											</Button>
										</div>
									{:else}
										<Button
											type="button"
											variant="secondary"
											size="sm"
											onclick={() => (confirmMove = true)}
											disabled={!moveTarget || isPending || !$content.isSuccess}
										>
											<ArrowRightLeft size={14} />
											{m.curated_move_action()}
										</Button>
									{/if}
								</div>
								<p class="mt-2 text-xs leading-relaxed text-[var(--color-text-muted)]">
									{m.curated_move_hint()}
								</p>
							{/if}
						</div>
					{/if}
				</div>

				<div>
					<label class="field-label" for="curated-title">{m.directives_field_title()}</label>
					<input
						id="curated-title"
						type="text"
						bind:value={form.title}
						required
						maxlength="512"
						class="field-input"
					/>
				</div>
				<div>
					<label class="field-label" for="curated-body">{m.ingest_field_body()}</label>
					<textarea
						id="curated-body"
						bind:value={form.body}
						required
						rows={14}
						class="field-textarea"
						placeholder={m.ingest_field_body_placeholder()}></textarea>
				</div>
				<div class="grid gap-4 sm:grid-cols-3">
					<div>
						<label class="field-label" for="curated-source-type">
							{m.ingest_col_source_type()}
						</label>
						<select id="curated-source-type" bind:value={form.sourceType} class="field-input">
							{#each MANUAL_SOURCE_TYPES as st (st)}
								<option value={st}>{st}</option>
							{/each}
						</select>
					</div>
					<div>
						<label class="field-label" for="curated-language">{m.common_lang()}</label>
						<select id="curated-language" bind:value={form.language} class="field-input">
							<option value="">{m.ingest_language_default()}</option>
							<option value="bg">bg</option>
							<option value="en">en</option>
						</select>
					</div>
					<div>
						<label class="field-label" for="curated-year">{m.common_reference_year()}</label>
						<input
							id="curated-year"
							type="number"
							bind:value={form.legalYear}
							placeholder={m.ingest_field_year_placeholder()}
							class="field-input"
						/>
					</div>
				</div>

				<div
					class="rounded-lg border px-3 py-2.5 text-xs leading-relaxed text-[var(--color-text-muted)]"
					style="background: var(--color-surface-2); border-color: var(--color-border);"
				>
					<p class="font-medium text-[var(--color-text)]">{m.curated_note_1()}</p>
					<p class="mt-1">{m.curated_note_2()}</p>
				</div>
			</div>

			{#if dialogError}
				<div
					class="mx-6 mb-2 rounded-lg px-3 py-2 text-sm"
					style="background: var(--color-danger-soft); color: var(--color-danger);"
				>
					{dialogError}
				</div>
			{/if}

			<div
				class="flex justify-end gap-2 border-t px-6 py-4"
				style="border-color: var(--color-border);"
			>
				<Button type="button" variant="secondary" size="sm" onclick={closeDialog}>
					{m.common_cancel()}
				</Button>
				<Button
					type="submit"
					size="sm"
					disabled={isPending || !form.title.trim() || !form.body.trim()}
				>
					{isPending ? m.ingest_saving() : m.ingest_save_entry()}
				</Button>
			</div>
		</form>
	{/if}
</dialog>
