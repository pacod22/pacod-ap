<script lang="ts">
	import { DEFAULT_PROFILE_ID } from '$lib/features/profiles/queries';
	import {
		Plus,
		Trash2,
		X,
		Check,
		Search,
		Link,
		FileUp,
		ExternalLink,
		RefreshCw,
		RotateCcw,
		Table2,
		PenLine
	} from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		createIngestListQuery,
		uploadFileMutation,
		ingestUrlMutation,
		importCsvMutation,
		ingestTextMutation,
		updateSourceStatusMutation,
		deleteIngestSourceMutation,
		MANUAL_SOURCE_TYPES,
		type IngestImportResult,
		type ManualSourceType,
		type Tier
	} from '$lib/features/ingest/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { m } from '$lib/paraglide/messages';

	// ---- Status display helpers ------------------------------------
	const STATUS_STYLES: Record<string, { bg: string; color: string }> = {
		NEW: { bg: 'var(--color-surface-3)', color: 'var(--color-text-muted)' },
		PROCESSING: {
			bg: 'color-mix(in oklch, var(--color-warning, #f59e0b) 15%, transparent)',
			color: 'var(--color-warning, #f59e0b)'
		},
		DONE: { bg: 'var(--color-success-soft)', color: 'var(--color-success)' },
		FAILED: { bg: 'var(--color-danger-soft)', color: 'var(--color-danger)' },
		OUTDATED: {
			bg: 'color-mix(in oklch, var(--color-warning, #f59e0b) 15%, transparent)',
			color: 'color-mix(in oklch, var(--color-warning, #f59e0b) 85%, var(--color-text))'
		}
	};

	function statusLabel(status: string): string {
		switch (status) {
			case 'NEW':
				return m.ingest_status_new();
			case 'PROCESSING':
				return m.ingest_status_processing();
			case 'DONE':
				return m.ingest_status_done();
			case 'FAILED':
				return m.ingest_status_failed();
			case 'OUTDATED':
				return m.ingest_status_outdated();
			default:
				return status;
		}
	}

	function statusConfig(status: string) {
		return {
			label: statusLabel(status),
			...(STATUS_STYLES[status] ?? {
				bg: 'var(--color-surface-3)',
				color: 'var(--color-text-muted)'
			})
		};
	}

	function formatDate(iso: string | null | undefined): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleDateString();
	}

	function tierLabel(tier: string | null | undefined): string {
		return tier === 'curated' ? m.ingest_tier_curated() : m.ingest_tier_authoritative();
	}

	// ---- Filters ----------------------------------------------------
	// Text/enum filters are client-side over the fetched page; `tier` is a SERVER
	// filter (see below) because the list is capped at 500 rows.
	let filterTitle = $state('');
	let filterSourceType = $state('');
	let filterYear = $state('');
	let filterLanguage = $state('');
	let filterStatus = $state('');
	let filterTier = $state<'' | Tier>('');

	const hasFilters = $derived(
		!!(
			filterTitle ||
			filterSourceType ||
			filterYear ||
			filterLanguage ||
			filterStatus ||
			filterTier
		)
	);

	function clearFilters() {
		filterTitle = '';
		filterSourceType = '';
		filterYear = '';
		filterLanguage = '';
		filterStatus = '';
		filterTier = '';
	}

	// ---- Queries & mutations ----------------------------------------
	// `filterTier` is part of the query (not the local derivation) so a tier filter
	// can't silently hide matches that fall past the 500-row fetch cap.
	const sources = $derived(createIngestListQuery(selectedProfile.id, filterTier || null));
	const uploadMut = uploadFileMutation();
	const urlMut = ingestUrlMutation();
	const importMut = importCsvMutation();
	const textMut = ingestTextMutation();
	const statusMut = updateSourceStatusMutation();
	const deleteMut = deleteIngestSourceMutation();

	const visibleSources = $derived(
		($sources.data ?? []).filter((s) => {
			const tf = filterTitle.trim().toLowerCase();
			const stf = filterSourceType.trim().toLowerCase();
			const yf = filterYear.trim();
			const lf = filterLanguage.trim().toLowerCase();
			const sf = filterStatus.trim();
			return (
				(!tf || s.title.toLowerCase().includes(tf)) &&
				(!stf || s.source_type.toLowerCase().includes(stf)) &&
				(!yf || String(s.legal_year) === yf) &&
				(!lf || s.language.toLowerCase().includes(lf)) &&
				(!sf || s.status === sf)
			);
		})
	);

	// ---- Dialog state ----------------------------------------------
	let dialog = $state<HTMLDialogElement | null>(null);
	let dialogMode = $state<'url' | 'file' | 'csv' | 'text'>('url');

	let urlForm = $state({ url: '', title: '', legalYear: '' });
	let fileForm = $state<{ file: File | null; legalYear: string; jsonMode: 'keep' | 'md' }>({
		file: null,
		legalYear: '',
		jsonMode: 'keep'
	});
	let csvForm = $state<{ file: File | null }>({ file: null });

	type TextForm = {
		title: string;
		body: string;
		tier: Tier;
		sourceType: ManualSourceType;
		language: '' | 'bg' | 'en';
		legalYear: string;
	};
	const emptyTextForm = (): TextForm => ({
		title: '',
		body: '',
		// Manual entry is the curated-knowledge path by default: hand-written notes
		// are lower authority than ingested primary sources, and the curated tier is
		// what retrieval down-weights accordingly.
		tier: 'curated',
		sourceType: 'curated_note',
		language: '',
		legalYear: ''
	});
	let textForm = $state<TextForm>(emptyTextForm());

	const fileIsJson = $derived(
		!!fileForm.file && fileForm.file.name.toLowerCase().endsWith('.json')
	);

	let dialogError = $state('');
	let dialogSuccess = $state<{ chunks: number; title: string } | null>(null);
	let importResult = $state<IngestImportResult | null>(null);

	const isPending = $derived($uploadMut.isPending || $urlMut.isPending || $textMut.isPending);

	function openDialog() {
		urlForm = { url: '', title: '', legalYear: '' };
		fileForm = { file: null, legalYear: '', jsonMode: 'keep' };
		csvForm = { file: null };
		textForm = emptyTextForm();
		dialogError = '';
		dialogSuccess = null;
		importResult = null;
		dialogMode = 'url';
		dialog?.showModal();
	}

	function closeDialog() {
		dialog?.close();
		dialogError = '';
		dialogSuccess = null;
		importResult = null;
	}

	function switchMode(mode: 'url' | 'file' | 'csv' | 'text') {
		dialogMode = mode;
		dialogError = '';
	}

	async function submitUrl() {
		if (!urlForm.url || !urlForm.title) return;
		dialogError = '';
		try {
			const res = await $urlMut.mutateAsync({
				url: urlForm.url,
				title: urlForm.title,
				legalYear: urlForm.legalYear ? Number(urlForm.legalYear) : undefined,
				profileId: selectedProfile.id ?? DEFAULT_PROFILE_ID
			});
			dialogSuccess = { chunks: res.chunks, title: res.source.title };
		} catch (e) {
			dialogError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	async function submitFile() {
		if (!fileForm.file) return;
		dialogError = '';
		try {
			const res = await $uploadMut.mutateAsync({
				file: fileForm.file,
				legalYear: fileForm.legalYear ? Number(fileForm.legalYear) : undefined,
				jsonMode: fileIsJson ? fileForm.jsonMode : undefined,
				profileId: selectedProfile.id ?? DEFAULT_PROFILE_ID
			});
			dialogSuccess = { chunks: res.chunks, title: res.source.title };
		} catch (e) {
			dialogError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	async function submitCsv() {
		if (!csvForm.file) return;
		dialogError = '';
		try {
			importResult = await $importMut.mutateAsync({
				file: csvForm.file,
				profileId: selectedProfile.id ?? DEFAULT_PROFILE_ID
			});
		} catch (e) {
			dialogError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	async function submitText() {
		if (!textForm.title.trim() || !textForm.body.trim()) return;
		dialogError = '';
		try {
			const res = await $textMut.mutateAsync({
				title: textForm.title.trim(),
				body: textForm.body,
				tier: textForm.tier,
				sourceType: textForm.sourceType,
				language: textForm.language || undefined,
				legalYear: textForm.legalYear ? Number(textForm.legalYear) : undefined,
				profileId: selectedProfile.id ?? DEFAULT_PROFILE_ID
			});
			dialogSuccess = { chunks: res.chunks, title: res.source.title };
		} catch (e) {
			dialogError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	// ---- Status update ---------------------------------------------
	async function setStatus(id: number, status: 'OUTDATED' | 'NEW') {
		try {
			await $statusMut.mutateAsync({ id, status });
		} catch {
			// error handled by mutation
		}
	}

	// ---- Delete ----------------------------------------------------
	let confirmDeleteId = $state<number | null>(null);

	async function confirmDelete(id: number) {
		try {
			await $deleteMut.mutateAsync(id);
			confirmDeleteId = null;
		} catch {
			// invalidation handled in mutation
		}
	}
</script>

<svelte:head><title>{m.ingest_title()}</title></svelte:head>

<div class="space-y-6">
	<!-- Header -->
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
		<div>
			<h1 class="text-2xl font-semibold tracking-tight">{m.nav_documents()}</h1>
			<p class="mt-1 text-sm text-[var(--color-text-muted)]">
				{m.ingest_intro()}
			</p>
		</div>
		<Button size="sm" onclick={openDialog} class="w-full sm:w-auto">
			<Plus size={14} />
			{m.ingest_add_doc()}
		</Button>
	</div>

	<!-- Filters -->
	<div
		class="flex flex-col flex-wrap gap-2 rounded-xl border p-3 sm:flex-row sm:items-center"
		style="background: var(--color-surface-2); border-color: var(--color-border);"
	>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-brand)_18%,transparent)] sm:w-56"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.ingest_filter_title()}
				bind:value={filterTitle}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-text-muted)] placeholder:opacity-70"
			/>
		</label>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-brand)_18%,transparent)] sm:w-36"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.ingest_filter_source_type()}
				bind:value={filterSourceType}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-text-muted)] placeholder:opacity-70"
			/>
		</label>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-brand)_18%,transparent)] sm:w-24"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.ingest_filter_year()}
				bind:value={filterYear}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-text-muted)] placeholder:opacity-70"
			/>
		</label>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-brand)_18%,transparent)] sm:w-28"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.ingest_filter_language()}
				bind:value={filterLanguage}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-text-muted)] placeholder:opacity-70"
			/>
		</label>
		<!-- Status select -->
		<select
			bind:value={filterStatus}
			class="h-10 w-full rounded-lg border px-3 text-sm transition-colors outline-none focus:border-[var(--color-brand)] focus:shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-brand)_18%,transparent)] sm:w-auto"
			style="background: var(--color-surface-2); border-color: var(--color-border); color: {filterStatus
				? 'var(--color-text)'
				: 'var(--color-text-muted)'};"
		>
			<option value="">{m.ingest_all_statuses()}</option>
			<option value="NEW">{m.ingest_status_new()}</option>
			<option value="PROCESSING">{m.ingest_status_processing()}</option>
			<option value="DONE">{m.ingest_status_done()}</option>
			<option value="FAILED">{m.ingest_status_failed()}</option>
			<option value="OUTDATED">{m.ingest_status_outdated()}</option>
		</select>
		<!-- Tier select — a SERVER filter, so it refetches rather than filtering locally -->
		<select
			bind:value={filterTier}
			class="h-10 w-full rounded-lg border px-3 text-sm transition-colors outline-none focus:border-[var(--color-brand)] focus:shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-brand)_18%,transparent)] sm:w-auto"
			style="background: var(--color-surface-2); border-color: var(--color-border); color: {filterTier
				? 'var(--color-text)'
				: 'var(--color-text-muted)'};"
		>
			<option value="">{m.ingest_all_tiers()}</option>
			<option value="authoritative">{m.ingest_tier_authoritative()}</option>
			<option value="curated">{m.ingest_tier_curated()}</option>
		</select>
		{#if hasFilters}
			<Button variant="ghost" size="sm" onclick={clearFilters}>
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
						<th>{m.ingest_col_source_type()}</th>
						<th>{m.ingest_col_tier()}</th>
						<th>{m.common_reference_year()}</th>
						<th>{m.common_lang()}</th>
						<th>{m.ingest_col_source()}</th>
						<th>{m.ingest_col_chunks()}</th>
						<th>{m.ingest_col_uploaded()}</th>
						<th>{m.ingest_col_ingested()}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#if $sources.isPending}
						<tr>
							<td colspan="11" class="py-12 text-center text-sm text-[var(--color-text-muted)]">
								{m.common_loading()}
							</td>
						</tr>
					{:else if $sources.isError}
						<tr>
							<td
								colspan="11"
								class="py-12 text-center text-sm"
								style="color: var(--color-danger);"
							>
								{m.ingest_load_failed()}
							</td>
						</tr>
					{:else if visibleSources.length > 0}
						{#each visibleSources as source (source.id)}
							{@const sc = statusConfig(source.status)}
							<tr>
								<td data-label={m.common_status()} class="w-24">
									<span
										class="rounded-md px-2 py-0.5 text-xs font-medium"
										style="background: {sc.bg}; color: {sc.color};"
									>
										{sc.label}
									</span>
								</td>
								<td data-label={m.directives_col_title()} class="stack-block max-w-xs">
									<span class="line-clamp-2 text-sm font-medium" title={source.title}>
										{source.title}
									</span>
								</td>
								<td data-label={m.ingest_col_source_type()}>
									<span
										class="rounded-md px-2 py-0.5 text-xs font-medium"
										style="background: var(--color-surface-3); color: var(--color-text-muted);"
									>
										{source.source_type}
									</span>
								</td>
								<td data-label={m.ingest_col_tier()}>
									<span
										class="rounded-md px-2 py-0.5 text-xs font-medium"
										style={source.tier === 'curated'
											? 'background: color-mix(in oklch, var(--color-brand) 15%, transparent); color: var(--color-brand);'
											: 'background: var(--color-surface-3); color: var(--color-text-muted);'}
									>
										{tierLabel(source.tier)}
									</span>
								</td>
								<td data-label={m.common_reference_year()} class="tabular-nums"
									>{source.legal_year}</td
								>
								<td data-label={m.common_lang()}>{source.language}</td>
								<td data-label={m.ingest_col_source()} class="stack-block max-w-[200px]">
									{#if source.url}
										<a
											href={source.url}
											target="_blank"
											rel="noopener noreferrer"
											class="flex items-center gap-1 truncate text-xs hover:underline"
											style="color: var(--color-brand);"
											title={source.url}
										>
											<ExternalLink size={11} class="shrink-0" />
											{source.url}
										</a>
									{:else if source.file_path}
										<span
											class="flex items-center gap-1 truncate text-xs text-[var(--color-text-muted)]"
											title={source.file_path}
										>
											<FileUp size={11} class="shrink-0" />
											{source.file_path}
										</span>
									{:else}
										<span class="text-[var(--color-text-muted)]">—</span>
									{/if}
								</td>
								<td data-label={m.ingest_col_chunks()} class="tabular-nums">
									<span
										class="rounded-md px-1.5 py-0.5 text-xs font-medium"
										style="background: var(--color-brand-soft); color: var(--color-brand);"
									>
										{source.chunk_count}
									</span>
								</td>
								<td
									data-label={m.ingest_col_uploaded()}
									class="text-[var(--color-text-muted)] tabular-nums"
									title={source.uploaded_at ?? undefined}
								>
									{formatDate(source.uploaded_at)}
								</td>
								<td
									data-label={m.ingest_col_ingested()}
									class="text-[var(--color-text-muted)] tabular-nums"
									title={source.ingested_at}
								>
									{formatDate(source.ingested_at)}
								</td>
								<td data-label="" class="w-24">
									{#if confirmDeleteId === source.id}
										<div class="flex items-center justify-end gap-1">
											<button
												onclick={() => confirmDelete(source.id)}
												class="touch-target rounded-lg p-1.5 transition-colors"
												style="color: var(--color-danger);"
												aria-label={m.common_confirm_delete()}
												title={m.common_confirm_delete()}
											>
												<Check size={14} />
											</button>
											<button
												onclick={() => (confirmDeleteId = null)}
												class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-3)]"
												aria-label={m.common_cancel()}
												title={m.common_cancel()}
											>
												<X size={14} />
											</button>
										</div>
									{:else}
										<div class="flex items-center justify-end gap-0.5">
											{#if source.status === 'DONE'}
												<button
													onclick={() => setStatus(source.id, 'OUTDATED')}
													disabled={$statusMut.isPending}
													class="touch-target hover:text-[var(--color-warning, #f59e0b)] rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors disabled:opacity-40"
													aria-label={m.ingest_mark_outdated()}
													title={m.ingest_mark_outdated_title()}
												>
													<RefreshCw size={14} />
												</button>
											{:else if source.status === 'OUTDATED' || source.status === 'FAILED'}
												<button
													onclick={() => setStatus(source.id, 'NEW')}
													disabled={$statusMut.isPending}
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-brand)] disabled:opacity-40"
													aria-label={m.ingest_mark_new()}
													title={m.ingest_mark_new_title()}
												>
													<RotateCcw size={14} />
												</button>
											{/if}
											<button
												onclick={() => (confirmDeleteId = source.id)}
												class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-danger)]"
												aria-label={m.common_delete()}
												title={m.common_delete()}
											>
												<Trash2 size={14} />
											</button>
										</div>
									{/if}
								</td>
							</tr>
						{/each}
					{:else}
						<tr>
							<td colspan="11" class="py-12 text-center text-sm text-[var(--color-text-muted)]">
								{hasFilters ? m.ingest_none_match() : m.ingest_empty()}
							</td>
						</tr>
					{/if}
				</tbody>
			</table>
		</div>
	</div>

	<!-- Result count -->
	{#if !$sources.isPending && ($sources.data?.length ?? 0) > 0}
		<p class="text-sm text-[var(--color-text-muted)]">
			{m.common_results({ n: visibleSources.length })}
			{#if hasFilters}
				{m.common_matching_filters()}{/if}
		</p>
	{/if}
</div>

<!-- ------------------------------------------------------------------ -->
<!-- Add document dialog -->
<!-- ------------------------------------------------------------------ -->
<dialog
	bind:this={dialog}
	class="mx-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-lg overflow-y-auto rounded-2xl p-0 shadow-2xl"
	style="background: var(--color-surface); border: 1px solid var(--color-border);"
>
	<!-- Header -->
	<div
		class="flex items-center justify-between border-b px-6 py-4"
		style="border-color: var(--color-border);"
	>
		<h2 class="text-base font-semibold">{m.ingest_add_doc()}</h2>
		<button
			type="button"
			onclick={closeDialog}
			class="rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
			aria-label={m.common_close()}
		>
			<X size={16} />
		</button>
	</div>

	{#if dialogSuccess}
		<!-- Success state -->
		<div class="px-6 py-8 text-center">
			<div
				class="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full"
				style="background: var(--color-success-soft);"
			>
				<Check size={22} style="color: var(--color-success);" />
			</div>
			<p class="font-semibold">{m.ingest_complete()}</p>
			<p class="mt-1 text-sm text-[var(--color-text-muted)]">
				<span class="font-medium" style="color: var(--color-text);">{dialogSuccess.title}</span>
				{m.ingest_was_split_into()}
				<span class="font-medium" style="color: var(--color-brand);">{dialogSuccess.chunks}</span>
				{m.ingest_chunks_suffix()}
			</p>
			<div class="mt-6">
				<Button size="sm" onclick={closeDialog}>{m.ingest_done()}</Button>
			</div>
		</div>
	{:else if importResult}
		<!-- CSV import summary -->
		<div class="px-6 py-6">
			<div class="flex flex-wrap gap-3">
				<div
					class="flex-1 rounded-xl border px-4 py-3 text-center"
					style="border-color: var(--color-border); background: var(--color-surface-2);"
				>
					<p class="text-2xl font-semibold tabular-nums" style="color: var(--color-success);">
						{importResult.created}
					</p>
					<p class="mt-0.5 text-xs text-[var(--color-text-muted)]">{m.ingest_created()}</p>
				</div>
				<div
					class="flex-1 rounded-xl border px-4 py-3 text-center"
					style="border-color: var(--color-border); background: var(--color-surface-2);"
				>
					<p
						class="text-2xl font-semibold tabular-nums"
						style="color: {importResult.skipped
							? 'var(--color-warning, #f59e0b)'
							: 'var(--color-text-muted)'};"
					>
						{importResult.skipped}
					</p>
					<p class="mt-0.5 text-xs text-[var(--color-text-muted)]">{m.ingest_skipped()}</p>
				</div>
				<div
					class="flex-1 rounded-xl border px-4 py-3 text-center"
					style="border-color: var(--color-border); background: var(--color-surface-2);"
				>
					<p class="text-2xl font-semibold tabular-nums" style="color: var(--color-text-muted);">
						{importResult.total_rows}
					</p>
					<p class="mt-0.5 text-xs text-[var(--color-text-muted)]">{m.ingest_total_rows()}</p>
				</div>
			</div>

			{#if importResult.errors.length > 0}
				<p class="mt-5 mb-2 text-sm font-medium">
					{m.ingest_rows_not_imported({ n: importResult.errors.length })}
				</p>
				<div
					class="max-h-56 overflow-y-auto rounded-lg border"
					style="border-color: var(--color-border);"
				>
					<table class="w-full text-left text-xs">
						<thead>
							<tr style="background: var(--color-surface-2);">
								<th class="px-3 py-1.5 font-medium text-[var(--color-text-muted)]"
									>{m.ingest_col_row()}</th
								>
								<th class="px-3 py-1.5 font-medium text-[var(--color-text-muted)]"
									>{m.ingest_col_source_id()}</th
								>
								<th class="px-3 py-1.5 font-medium text-[var(--color-text-muted)]"
									>{m.redis_col_reason()}</th
								>
							</tr>
						</thead>
						<tbody>
							{#each importResult.errors as err (err.row)}
								<tr class="border-t" style="border-color: var(--color-border);">
									<td class="px-3 py-1.5 tabular-nums">{err.row}</td>
									<td class="px-3 py-1.5 font-mono">{err.source_id ?? '—'}</td>
									<td class="px-3 py-1.5" style="color: var(--color-danger);">{err.error}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{:else}
				<p class="mt-5 text-sm text-[var(--color-text-muted)]">{m.ingest_all_rows_imported()}</p>
			{/if}

			<div class="mt-6 flex justify-end">
				<Button size="sm" onclick={closeDialog}>{m.ingest_done()}</Button>
			</div>
		</div>
	{:else}
		<!-- Tab switcher -->
		<div class="flex border-b px-6 pt-4" style="border-color: var(--color-border);">
			<button
				type="button"
				onclick={() => switchMode('url')}
				class="flex items-center gap-2 border-b-2 pr-4 pb-3 text-sm font-medium transition-colors"
				style={dialogMode === 'url'
					? 'border-color: var(--color-brand); color: var(--color-brand);'
					: 'border-color: transparent; color: var(--color-text-muted);'}
			>
				<Link size={14} />
				{m.ingest_from_url()}
			</button>
			<button
				type="button"
				onclick={() => switchMode('file')}
				class="flex items-center gap-2 border-b-2 px-4 pb-3 text-sm font-medium transition-colors"
				style={dialogMode === 'file'
					? 'border-color: var(--color-brand); color: var(--color-brand);'
					: 'border-color: transparent; color: var(--color-text-muted);'}
			>
				<FileUp size={14} />
				{m.ingest_upload_file()}
			</button>
			<button
				type="button"
				onclick={() => switchMode('csv')}
				class="flex items-center gap-2 border-b-2 px-4 pb-3 text-sm font-medium transition-colors"
				style={dialogMode === 'csv'
					? 'border-color: var(--color-brand); color: var(--color-brand);'
					: 'border-color: transparent; color: var(--color-text-muted);'}
			>
				<Table2 size={14} />
				{m.ingest_import_csv()}
			</button>
			<button
				type="button"
				onclick={() => switchMode('text')}
				class="flex items-center gap-2 border-b-2 pb-3 pl-4 text-sm font-medium transition-colors"
				style={dialogMode === 'text'
					? 'border-color: var(--color-brand); color: var(--color-brand);'
					: 'border-color: transparent; color: var(--color-text-muted);'}
			>
				<PenLine size={14} />
				{m.ingest_manual_entry()}
			</button>
		</div>

		<!-- URL form -->
		{#if dialogMode === 'url'}
			<form
				onsubmit={(e) => {
					e.preventDefault();
					submitUrl();
				}}
			>
				<div class="space-y-4 px-6 py-5">
					<div>
						<label class="field-label" for="ingest-url">{m.ingest_field_url()}</label>
						<input
							id="ingest-url"
							type="url"
							bind:value={urlForm.url}
							required
							placeholder="https://…"
							class="field-input"
						/>
					</div>
					<div>
						<label class="field-label" for="ingest-title">{m.directives_field_title()}</label>
						<input
							id="ingest-title"
							type="text"
							bind:value={urlForm.title}
							required
							maxlength="512"
							placeholder={m.ingest_title_ph()}
							class="field-input"
						/>
					</div>
					<div>
						<label class="field-label" for="ingest-url-year"
							>{m.directives_field_legal_year()}</label
						>
						<input
							id="ingest-url-year"
							type="number"
							bind:value={urlForm.legalYear}
							min="2000"
							max="2100"
							placeholder={m.ingest_year_ph()}
							class="field-input"
						/>
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
					<Button type="submit" size="sm" disabled={isPending}>
						{isPending ? m.ingest_ingesting() : m.ingest_ingest_url()}
					</Button>
				</div>
			</form>
		{:else if dialogMode === 'file'}
			<!-- File upload form -->
			<form
				onsubmit={(e) => {
					e.preventDefault();
					submitFile();
				}}
			>
				<div class="space-y-4 px-6 py-5">
					<div>
						<label class="field-label" for="ingest-file">{m.ingest_field_file()}</label>
						<input
							id="ingest-file"
							type="file"
							accept=".pdf,.docx,.txt,.html,.htm,.md,.json"
							required
							onchange={(e) => {
								fileForm.file = (e.currentTarget as HTMLInputElement).files?.[0] ?? null;
							}}
							class="field-input py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:px-3 file:py-1 file:text-xs file:font-medium"
							style="file-selector-button: background: var(--color-surface-3); color: var(--color-text);"
						/>
						<p class="mt-1 text-xs text-[var(--color-text-muted)]">
							{m.ingest_supported()}
						</p>
					</div>

					{#if fileIsJson}
						<div>
							<span class="field-label">{m.ingest_json_handling()}</span>
							<div class="flex flex-col gap-2">
								<label class="flex items-center gap-2 text-sm">
									<input type="radio" value="keep" bind:group={fileForm.jsonMode} />
									{m.ingest_keep_json()}
									<span class="text-xs text-[var(--color-text-muted)]"
										>{m.ingest_keep_json_note()}</span
									>
								</label>
								<label class="flex items-center gap-2 text-sm">
									<input type="radio" value="md" bind:group={fileForm.jsonMode} />
									{m.ingest_convert_md()}
									<span class="text-xs text-[var(--color-text-muted)]"
										>{m.ingest_convert_md_note()}</span
									>
								</label>
							</div>
						</div>
					{/if}
					<div>
						<label class="field-label" for="ingest-file-year"
							>{m.directives_field_legal_year()}</label
						>
						<input
							id="ingest-file-year"
							type="number"
							bind:value={fileForm.legalYear}
							min="2000"
							max="2100"
							placeholder={m.ingest_year_ph()}
							class="field-input"
						/>
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
					<Button type="submit" size="sm" disabled={isPending || !fileForm.file}>
						{isPending ? m.ingest_ingesting() : m.ingest_upload_and_ingest()}
					</Button>
				</div>
			</form>
		{:else if dialogMode === 'csv'}
			<!-- CSV metadata import form -->
			<form
				onsubmit={(e) => {
					e.preventDefault();
					submitCsv();
				}}
			>
				<div class="space-y-4 px-6 py-5">
					<div>
						<label class="field-label" for="ingest-csv">{m.ingest_csv_file()}</label>
						<input
							id="ingest-csv"
							type="file"
							accept=".csv,text/csv"
							required
							onchange={(e) => {
								csvForm.file = (e.currentTarget as HTMLInputElement).files?.[0] ?? null;
							}}
							class="field-input py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:px-3 file:py-1 file:text-xs file:font-medium"
							style="file-selector-button: background: var(--color-surface-3); color: var(--color-text);"
						/>
					</div>
					<div
						class="rounded-lg border px-3 py-2.5 text-xs leading-relaxed text-[var(--color-text-muted)]"
						style="background: var(--color-surface-2); border-color: var(--color-border);"
					>
						<p class="font-medium text-[var(--color-text)]">{m.ingest_csv_note_1()}</p>
						<p class="mt-1">
							{m.ingest_csv_note_2()}
						</p>
						<p class="mt-1">
							{m.ingest_csv_note_3()}
						</p>
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
					<Button type="submit" size="sm" disabled={$importMut.isPending || !csvForm.file}>
						{$importMut.isPending ? m.ingest_importing() : m.ingest_import_csv()}
					</Button>
				</div>
			</form>

			<!-- Manual entry form — type knowledge straight into the KB -->
		{:else if dialogMode === 'text'}
			<form
				onsubmit={(e) => {
					e.preventDefault();
					submitText();
				}}
			>
				<div class="space-y-4 px-6 py-5">
					<div>
						<label class="field-label" for="ingest-text-title">{m.directives_field_title()}</label>
						<input
							id="ingest-text-title"
							type="text"
							bind:value={textForm.title}
							required
							maxlength="512"
							class="field-input"
						/>
					</div>
					<div>
						<label class="field-label" for="ingest-text-body">{m.ingest_field_body()}</label>
						<textarea
							id="ingest-text-body"
							bind:value={textForm.body}
							required
							rows={10}
							class="field-textarea"
							placeholder={m.ingest_field_body_placeholder()}></textarea>
					</div>
					<div class="grid gap-4 sm:grid-cols-2">
						<div>
							<label class="field-label" for="ingest-text-tier">{m.ingest_col_tier()}</label>
							<select id="ingest-text-tier" bind:value={textForm.tier} class="field-input">
								<option value="curated">{m.ingest_tier_curated()}</option>
								<option value="authoritative">{m.ingest_tier_authoritative()}</option>
							</select>
						</div>
						<div>
							<label class="field-label" for="ingest-text-source-type">
								{m.ingest_col_source_type()}
							</label>
							<select
								id="ingest-text-source-type"
								bind:value={textForm.sourceType}
								class="field-input"
							>
								{#each MANUAL_SOURCE_TYPES as st (st)}
									<option value={st}>{st}</option>
								{/each}
							</select>
						</div>
						<div>
							<label class="field-label" for="ingest-text-language">{m.common_lang()}</label>
							<select id="ingest-text-language" bind:value={textForm.language} class="field-input">
								<option value="">{m.ingest_language_default()}</option>
								<option value="bg">bg</option>
								<option value="en">en</option>
							</select>
						</div>
						<div>
							<label class="field-label" for="ingest-text-year">{m.common_reference_year()}</label>
							<input
								id="ingest-text-year"
								type="number"
								bind:value={textForm.legalYear}
								placeholder={m.ingest_field_year_placeholder()}
								class="field-input"
							/>
						</div>
					</div>
					<div
						class="rounded-lg border px-3 py-2.5 text-xs leading-relaxed text-[var(--color-text-muted)]"
						style="background: var(--color-surface-2); border-color: var(--color-border);"
					>
						<p class="font-medium text-[var(--color-text)]">{m.ingest_manual_note_1()}</p>
						<p class="mt-1">{m.ingest_manual_note_2()}</p>
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
						disabled={isPending || !textForm.title.trim() || !textForm.body.trim()}
					>
						{isPending ? m.ingest_saving() : m.ingest_save_entry()}
					</Button>
				</div>
			</form>
		{/if}
	{/if}
</dialog>
