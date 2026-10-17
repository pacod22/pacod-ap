<script lang="ts">
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import {
		Database,
		Trash2,
		AlertTriangle,
		Search,
		X,
		ExternalLink,
		Sparkles,
		Plus,
		ArrowRight,
		Upload,
		Download
	} from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		createCollectionInfoQuery,
		activeCollectionQuery,
		createPointsQuery,
		createFacetsQuery,
		fetchPointsExport,
		createPointMutation,
		importPointsMutation,
		deletePointMutation,
		bulkDeleteMutation,
		searchVectorStoreMutation,
		type PointsFilters,
		type SearchResultItem
	} from '$lib/features/knowledge/queries';
	import { hasRole } from '$lib/types/auth';
	import { m } from '$lib/paraglide/messages';

	let { data } = $props();

	const isAdmin = $derived(hasRole(data.user, 'admin'));
	const pid = $derived(selectedProfile.id);
	const info = $derived(createCollectionInfoQuery(pid));
	const active = $derived(activeCollectionQuery(pid));
	// bge-m3 dim; add/edit embed at this size, so warn if the active collection differs.
	const vectorDim = $derived($info.data ? Number($info.data.config?.vector_size ?? 0) : 0);

	// ---- Tab --------------------------------------------------------
	let activeTab = $state<'browse' | 'search'>('browse');

	// ---- Browse: pagination & filters -------------------------------
	// Filters are applied server-side (Qdrant scroll_filter) so every page
	// returns up to `pageSize` matching points — not a thinned-out client view.
	const PAGE_SIZE_OPTIONS = [10, 20, 50, 100, 200];

	let cursor = $state<string | null>(null);
	let filterSourceType = $state('');
	let filterLanguage = $state('');
	let filterTitle = $state('');
	let pageSize = $state(20);

	// Distinct source_type / title values for the filter dropdowns.
	const facets = $derived(createFacetsQuery(pid));

	const apiFilters = $derived<PointsFilters>({
		...(filterSourceType ? { source_type: filterSourceType } : {}),
		...(filterLanguage ? { language: filterLanguage } : {}),
		...(filterTitle ? { title: filterTitle } : {})
	});

	// Any filter or page-size change invalidates the offset cursor (a stale
	// cursor points nowhere under a new filter/limit), so reset to the first page.
	function resetPaging() {
		cursor = null;
	}

	function clearFilters() {
		filterSourceType = '';
		filterLanguage = '';
		filterTitle = '';
		cursor = null;
	}

	const hasActiveFilters = $derived(!!filterSourceType || !!filterLanguage || !!filterTitle);

	const points = $derived(createPointsQuery(pageSize, cursor, apiFilters, pid));

	// ---- Export all points (admin) ----------------------------------
	let exporting = $state(false);
	let exportError = $state('');

	async function handleExport() {
		exportError = '';
		exporting = true;
		try {
			const dump = await fetchPointsExport(pid);
			const blob = new Blob([JSON.stringify(dump, null, 2)], { type: 'application/json' });
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
			a.download = `${dump.collection}-points-${stamp}.json`;
			document.body.appendChild(a);
			a.click();
			a.remove();
			URL.revokeObjectURL(url);
		} catch (e) {
			exportError = e instanceof Error ? e.message : m.points_export_failed();
		} finally {
			exporting = false;
		}
	}

	const deletePoint = $derived(deletePointMutation(pid));
	const bulkDelete = $derived(bulkDeleteMutation(pid));
	let bulkYear = $state('');

	// ---- Add point (admin) ------------------------------------------
	const createPoint = $derived(createPointMutation(pid));
	let showAddPoint = $state(false);
	let addText = $state('');
	let addTitle = $state('');
	let addSourceId = $state('');
	let addSourceType = $state('document');
	let addLanguage = $state<'bg' | 'en'>('bg');
	let addYear = $state('');
	let addUrl = $state('');
	let addMessage = $state('');
	let addError = $state('');

	function resetAddForm() {
		addText = '';
		addTitle = '';
		addSourceId = '';
		addSourceType = 'document';
		addLanguage = 'bg';
		addYear = '';
		addUrl = '';
	}

	async function handleCreatePoint() {
		addMessage = '';
		addError = '';
		if (!addText.trim() || !addSourceId.trim()) {
			addError = m.points_text_source_required();
			return;
		}
		try {
			const created = await $createPoint.mutateAsync({
				text: addText.trim(),
				title: addTitle.trim(),
				source_id: addSourceId.trim(),
				source_type: addSourceType.trim() || 'document',
				language: addLanguage,
				legal_year: addYear ? Number(addYear) : null,
				url: addUrl.trim() || null
			});
			addMessage = m.points_created({ id: created.id });
			resetAddForm();
			cursor = null;
		} catch (e) {
			addError = e instanceof Error ? e.message : m.points_create_failed();
		}
	}

	// ---- Import points from JSON (admin) ----------------------------
	const importPoints = $derived(importPointsMutation(pid));
	let importFile = $state<File | null>(null);
	let importInput = $state<HTMLInputElement | null>(null);
	let importMessage = $state('');
	let importError = $state('');

	async function handleImport() {
		importMessage = '';
		importError = '';
		if (!importFile) return;
		let dump: unknown;
		try {
			dump = JSON.parse(await importFile.text());
		} catch {
			importError = m.points_not_json();
			return;
		}
		const points = (dump as { points?: unknown })?.points;
		if (!Array.isArray(points) || points.length === 0) {
			importError = m.points_json_needs_array();
			return;
		}
		const src = (dump as { collection?: string })?.collection;
		const target = $info.data?.name ?? $active.data?.active ?? m.points_the_active_collection();
		if (
			!confirm(
				m.points_import_confirm({ n: points.length, target }) +
					(src && src !== target ? m.points_import_note({ src }) : '')
			)
		)
			return;
		try {
			const res = await $importPoints.mutateAsync(dump);
			importMessage =
				m.points_imported({ n: res.imported, collection: res.collection }) +
				(res.skipped ? m.points_imported_skipped({ n: res.skipped }) : '');
			importFile = null;
			if (importInput) importInput.value = '';
			cursor = null;
		} catch (e) {
			importError = e instanceof Error ? e.message : m.points_import_failed();
		}
	}

	async function handleBulkDelete() {
		if (!bulkYear) return;
		if (!confirm(m.points_bulk_delete_confirm({ year: bulkYear }))) return;
		await $bulkDelete.mutateAsync({ legal_year: Number(bulkYear) });
		cursor = null;
	}

	// ---- Search -----------------------------------------------------
	const search = $derived(searchVectorStoreMutation(pid));

	let searchQuery = $state('');
	let searchYear = $state('');
	let searchLanguage = $state('');
	let searchTopK = $state(10);
	let searchRerank = $state(true);
	let searchResults = $state<SearchResultItem[]>([]);
	let searchDone = $state(false);

	// A profile switch targets a different collection: the scroll cursor and the
	// facet-derived filters are point ids / values from the previous collection
	// (a foreign offset_id makes Qdrant return nothing or fail), and search hits
	// belong to the old KB. Reset to a clean first page for the new profile.
	$effect(() => {
		void pid;
		clearFilters();
		searchResults = [];
		searchDone = false;
	});

	async function handleSearch() {
		if (!searchQuery.trim()) return;
		const results = await $search.mutateAsync({
			query: searchQuery.trim(),
			legal_year: searchYear ? Number(searchYear) : null,
			language: searchLanguage || null,
			top_k: searchTopK,
			rerank: searchRerank
		});
		searchResults = results;
		searchDone = true;
	}

	function handleSearchKey(e: KeyboardEvent) {
		if (e.key === 'Enter') handleSearch();
	}

	function scoreColor(score: number): string {
		if (score >= 0.75) return 'background: var(--color-success-soft); color: var(--color-success);';
		if (score >= 0.5) return 'background: var(--color-warning-soft); color: var(--color-warning);';
		return 'background: var(--color-surface-3); color: var(--color-text-muted);';
	}
</script>

<svelte:head><title>{m.points_title()}</title></svelte:head>

<div class="space-y-6">
	<!-- Header -->
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">{m.nav_points()}</h1>
		<p class="mt-1 text-sm text-[var(--color-text-muted)]">
			{m.points_intro()}
		</p>
	</div>

	<!-- Active-collection context banner -->
	<div
		class="flex flex-wrap items-center justify-between gap-3 rounded-xl border p-4"
		style="background: var(--color-surface-2); border-color: var(--color-border);"
	>
		<div class="flex items-center gap-3">
			<div
				class="flex h-9 w-9 items-center justify-center rounded-lg"
				style="background: var(--color-brand-soft);"
			>
				<Database size={16} style="color: var(--color-brand);" />
			</div>
			<div>
				<div class="text-xs font-medium tracking-wide text-[var(--color-text-muted)] uppercase">
					{m.collections_active_collection()}
				</div>
				<div class="flex items-center gap-2">
					<span class="font-mono text-sm font-semibold"
						>{$info.data?.name ?? $active.data?.active ?? '…'}</span
					>
					{#if $info.data}
						<span class="text-xs text-[var(--color-text-muted)]"
							>{m.points_count_suffix({ n: $info.data.points_count.toLocaleString() })}</span
						>
					{/if}
					{#if $active.data?.is_override}
						<span
							class="rounded-full px-2 py-0.5 text-[10px] font-medium"
							style="background: var(--color-warning-soft); color: var(--color-warning);"
							>{m.collections_override()}</span
						>
					{/if}
				</div>
			</div>
		</div>
		<a
			href="/collections"
			class="flex items-center gap-1 text-sm font-medium"
			style="color: var(--color-brand);"
		>
			{m.points_manage_collections()}
			<ArrowRight size={14} />
		</a>
	</div>

	{#if $info.data && vectorDim && vectorDim !== 1024}
		<div
			class="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
			style="background: var(--color-warning-soft); color: var(--color-warning);"
		>
			<AlertTriangle size={14} />
			{m.points_dim_warning({ dim: vectorDim })}
		</div>
	{/if}

	<!-- Tab switcher -->
	<div
		class="flex gap-1 rounded-xl border p-1"
		style="background: var(--color-surface-2); border-color: var(--color-border); width: fit-content;"
	>
		<button
			onclick={() => (activeTab = 'browse')}
			class="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors"
			style={activeTab === 'browse'
				? 'background: var(--color-surface); color: var(--color-text); box-shadow: 0 1px 3px color-mix(in oklch, var(--color-text) 10%, transparent);'
				: 'color: var(--color-text-muted);'}
		>
			<Database size={14} />
			{m.points_tab_browse()}
		</button>
		<button
			onclick={() => (activeTab = 'search')}
			class="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors"
			style={activeTab === 'search'
				? 'background: var(--color-surface); color: var(--color-text); box-shadow: 0 1px 3px color-mix(in oklch, var(--color-text) 10%, transparent);'
				: 'color: var(--color-text-muted);'}
		>
			<Sparkles size={14} />
			{m.points_tab_search()}
		</button>
	</div>

	<!-- ================================================================ -->
	<!-- SEARCH TAB                                                        -->
	<!-- ================================================================ -->
	{#if activeTab === 'search'}
		<div class="space-y-4">
			<!-- Search form -->
			<div
				class="rounded-xl border p-5"
				style="background: var(--color-surface-2); border-color: var(--color-border);"
			>
				<!-- Query input -->
				<label
					class="mb-4 flex items-center gap-3 rounded-xl border px-4 py-3 transition-colors focus-within:border-[var(--color-brand)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-brand)_18%,transparent)]"
					style="background: var(--color-surface); border-color: var(--color-border);"
				>
					<Search size={18} class="shrink-0 text-[var(--color-text-muted)]" />
					<input
						type="text"
						placeholder={m.points_search_placeholder()}
						bind:value={searchQuery}
						onkeydown={handleSearchKey}
						class="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-text-muted)] placeholder:opacity-60"
					/>
					{#if searchQuery}
						<button
							onclick={() => {
								searchQuery = '';
								searchResults = [];
								searchDone = false;
							}}
							class="no-border shrink-0 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
						>
							<X size={15} />
						</button>
					{/if}
				</label>

				<!-- Options row -->
				<div class="flex flex-wrap items-center gap-3">
					<label
						class="flex h-9 items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)]"
						style="background: var(--color-surface); border-color: var(--color-border);"
					>
						<span class="shrink-0 text-xs text-[var(--color-text-muted)]"
							>{m.common_reference_year()}</span
						>
						<input
							type="text"
							placeholder="all"
							bind:value={searchYear}
							class="no-border w-16 bg-transparent text-sm outline-none placeholder:text-[var(--color-text-muted)] placeholder:opacity-60"
						/>
					</label>

					<label
						class="flex h-9 items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)]"
						style="background: var(--color-surface); border-color: var(--color-border);"
					>
						<span class="shrink-0 text-xs text-[var(--color-text-muted)]"
							>{m.directives_field_language()}</span
						>
						<select
							bind:value={searchLanguage}
							class="no-border bg-transparent text-sm outline-none"
						>
							<option value="">all</option>
							<option value="bg">bg</option>
							<option value="en">en</option>
						</select>
					</label>

					<label
						class="flex h-9 items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)]"
						style="background: var(--color-surface); border-color: var(--color-border);"
					>
						<span class="shrink-0 text-xs text-[var(--color-text-muted)]">{m.points_top_k()}</span>
						<input
							type="number"
							min="1"
							max="100"
							bind:value={searchTopK}
							class="no-border w-14 bg-transparent text-sm outline-none"
						/>
					</label>

					<label
						class="flex h-9 cursor-pointer items-center gap-2 rounded-lg border px-3"
						style="background: var(--color-surface); border-color: var(--color-border);"
					>
						<input
							type="checkbox"
							bind:checked={searchRerank}
							class="no-border accent-[var(--color-brand)]"
						/>
						<span class="text-xs text-[var(--color-text-muted)]">{m.points_rerank()}</span>
					</label>

					<Button
						variant="primary"
						size="sm"
						onclick={handleSearch}
						disabled={!searchQuery.trim() || $search.isPending}
					>
						<Search size={14} />
						{$search.isPending ? m.points_searching() : m.points_search_btn()}
					</Button>
				</div>
			</div>

			<!-- Results -->
			{#if $search.isError}
				<div
					class="rounded-xl border p-6 text-center text-sm"
					style="color: var(--color-danger); border-color: var(--color-border);"
				>
					{m.points_search_failed({ error: ($search.error as Error).message })}
				</div>
			{:else if $search.isPending}
				<div
					class="flex h-32 items-center justify-center rounded-xl border text-sm text-[var(--color-text-muted)]"
					style="border-color: var(--color-border); background: var(--color-surface-2);"
				>
					{m.points_embedding()}
				</div>
			{:else if searchDone && searchResults.length === 0}
				<div
					class="flex h-32 flex-col items-center justify-center gap-2 rounded-xl border text-sm text-[var(--color-text-muted)]"
					style="border-color: var(--color-border); background: var(--color-surface-2);"
				>
					<Sparkles size={24} />
					{m.points_no_results()}
				</div>
			{:else if searchResults.length > 0}
				<div class="mb-2 flex items-center justify-between">
					<span class="text-xs text-[var(--color-text-muted)]">
						{m.common_results({ n: searchResults.length })}
						{searchRerank ? m.points_reranked() : ''}
					</span>
				</div>
				<div class="space-y-3">
					{#each searchResults as result, i (result.chunk_id)}
						<div
							class="rounded-xl border p-5 transition-colors hover:border-[var(--color-brand)]"
							style="background: var(--color-surface-2); border-color: var(--color-border);"
						>
							<!-- Header row -->
							<div class="mb-3 flex items-start justify-between gap-3">
								<div class="flex items-center gap-2">
									<span class="text-xs font-semibold text-[var(--color-text-muted)] tabular-nums">
										#{i + 1}
									</span>
									<span
										class="rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums"
										style={scoreColor(result.score)}
									>
										{(result.score * 100).toFixed(1)}%
									</span>
									<span class="text-sm leading-tight font-semibold">
										{result.title}
									</span>
								</div>
								<div class="flex shrink-0 items-center gap-2">
									{#if result.url}
										<a
											href={result.url}
											target="_blank"
											rel="noopener noreferrer"
											class="text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-brand)]"
											title={result.url}
										>
											<ExternalLink size={14} />
										</a>
									{/if}
									<span
										class="rounded-full px-2 py-0.5 text-[10px] font-medium"
										style="background: var(--color-brand-soft); color: var(--color-brand);"
									>
										{result.source_type}
									</span>
									<span class="text-xs text-[var(--color-text-muted)]">{result.language}</span>
									{#if result.legal_year}
										<span class="text-xs text-[var(--color-text-muted)]">{result.legal_year}</span>
									{/if}
								</div>
							</div>

							<!-- Text chunk -->
							<p class="text-sm leading-relaxed text-[var(--color-text-muted)]">
								{result.text}
							</p>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	<!-- ================================================================ -->
	<!-- BROWSE TAB                                                        -->
	<!-- ================================================================ -->
	{#if activeTab === 'browse'}
		<!-- Import points from JSON (admin only) -->
		{#if isAdmin}
			<div
				class="rounded-xl border p-5"
				style="background: var(--color-surface-2); border-color: var(--color-border);"
			>
				<div class="mb-1 flex items-center gap-2">
					<Upload size={15} style="color: var(--color-brand);" />
					<h2 class="text-sm font-semibold">{m.points_import_heading()}</h2>
				</div>
				<p class="mb-4 text-xs text-[var(--color-text-muted)]">
					{m.points_import_hint()}
				</p>
				<div class="flex flex-wrap items-center gap-3">
					<label
						class="flex h-9 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm"
						style="background: var(--color-surface); border-color: var(--color-border);"
					>
						<Upload size={14} class="text-[var(--color-text-muted)]" />
						<span class="text-[var(--color-text-muted)]"
							>{importFile ? importFile.name : m.points_choose_json()}</span
						>
						<input
							bind:this={importInput}
							type="file"
							accept=".json,application/json"
							class="hidden"
							onchange={(e) =>
								(importFile = (e.currentTarget as HTMLInputElement).files?.[0] ?? null)}
						/>
					</label>
					<Button
						variant="primary"
						size="sm"
						onclick={handleImport}
						disabled={!importFile || $importPoints.isPending}
					>
						<Upload size={14} />
						{$importPoints.isPending ? 'Importing…' : 'Import'}
					</Button>
				</div>
				{#if importMessage}
					<div
						class="mt-3 rounded-lg px-3 py-2 text-xs"
						style="background: var(--color-success-soft); color: var(--color-success);"
					>
						{importMessage}
					</div>
				{/if}
				{#if importError}
					<div
						class="mt-3 rounded-lg px-3 py-2 text-xs"
						style="background: var(--color-danger-soft); color: var(--color-danger);"
					>
						{importError}
					</div>
				{/if}
			</div>
		{/if}

		<!-- Add point (admin only) -->
		{#if isAdmin}
			<div
				class="rounded-xl border p-5"
				style="background: var(--color-surface-2); border-color: var(--color-border);"
			>
				<div class="flex items-center justify-between">
					<div class="flex items-center gap-2">
						<Plus size={15} style="color: var(--color-brand);" />
						<h2 class="text-sm font-semibold">{m.points_add_point()}</h2>
					</div>
					<Button variant="secondary" size="sm" onclick={() => (showAddPoint = !showAddPoint)}>
						{showAddPoint ? m.common_cancel() : m.points_new_point()}
					</Button>
				</div>

				{#if showAddPoint}
					<p class="mt-2 mb-4 text-xs text-[var(--color-text-muted)]">
						{m.points_add_hint()}
					</p>

					<div class="space-y-3">
						<div>
							<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]"
								>{m.points_field_text()}</span
							>
							<textarea
								bind:value={addText}
								rows="4"
								placeholder={m.points_text_ph()}
								class="field-input w-full"></textarea>
						</div>

						<div class="grid gap-3 sm:grid-cols-2">
							<div>
								<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]"
									>{m.point_field_title()}</span
								>
								<input
									type="text"
									bind:value={addTitle}
									class="field-input w-full"
									placeholder={m.points_title_ph()}
								/>
							</div>
							<div>
								<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]"
									>{m.points_field_source_id()}</span
								>
								<input
									type="text"
									bind:value={addSourceId}
									class="field-input w-full"
									placeholder={m.points_source_id_ph()}
								/>
							</div>
							<div>
								<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]"
									>{m.ingest_col_source_type()}</span
								>
								<input
									type="text"
									bind:value={addSourceType}
									class="field-input w-full"
									placeholder="document"
								/>
							</div>
							<div>
								<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]"
									>{m.directives_field_language()}</span
								>
								<select bind:value={addLanguage} class="field-input w-full">
									<option value="bg">bg</option>
									<option value="en">en</option>
								</select>
							</div>
							<div>
								<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]"
									>{m.directives_field_legal_year()}</span
								>
								<input
									type="text"
									bind:value={addYear}
									class="field-input w-full"
									placeholder={m.points_year_ph()}
								/>
							</div>
							<div>
								<span class="mb-1 block text-xs font-medium text-[var(--color-text-muted)]"
									>URL</span
								>
								<input
									type="text"
									bind:value={addUrl}
									class="field-input w-full"
									placeholder="https://…"
								/>
							</div>
						</div>

						{#if addMessage}
							<div
								class="rounded-lg px-3 py-2 text-xs"
								style="background: var(--color-success-soft); color: var(--color-success);"
							>
								{addMessage}
							</div>
						{/if}
						{#if addError}
							<div
								class="rounded-lg px-3 py-2 text-xs"
								style="background: var(--color-danger-soft); color: var(--color-danger);"
							>
								{addError}
							</div>
						{/if}

						<div class="flex items-center gap-3">
							<Button
								variant="primary"
								size="sm"
								onclick={handleCreatePoint}
								disabled={!addText.trim() || !addSourceId.trim() || $createPoint.isPending}
							>
								<Plus size={14} />
								{$createPoint.isPending ? 'Embedding…' : 'Create point'}
							</Button>
						</div>
					</div>
				{:else if addMessage}
					<div
						class="mt-3 rounded-lg px-3 py-2 text-xs"
						style="background: var(--color-success-soft); color: var(--color-success);"
					>
						{addMessage}
					</div>
				{/if}
			</div>
		{/if}

		<!-- Bulk delete (admin only) -->
		{#if isAdmin}
			<div
				class="rounded-xl border p-5"
				style="background: var(--color-surface-2); border-color: var(--color-border);"
			>
				<div class="mb-3 flex items-center gap-2">
					<AlertTriangle size={15} style="color: var(--color-warning);" />
					<h2 class="text-sm font-semibold">{m.points_bulk_heading()}</h2>
				</div>
				<p class="mb-4 text-xs text-[var(--color-text-muted)]">
					{m.points_bulk_hint()}
				</p>
				<div class="flex flex-col gap-3 sm:flex-row sm:items-center">
					<input
						type="text"
						placeholder={m.points_bulk_year_ph()}
						bind:value={bulkYear}
						class="field-input w-full sm:w-36"
					/>
					<Button
						variant="danger"
						size="sm"
						onclick={handleBulkDelete}
						disabled={!bulkYear || $bulkDelete.isPending}
						class="w-full sm:w-auto"
					>
						<Trash2 size={14} />
						{$bulkDelete.isPending ? 'Deleting…' : 'Delete all for year'}
					</Button>
				</div>
			</div>
		{/if}

		<!-- Points table -->
		<div>
			<div class="mb-3 flex flex-wrap items-center justify-between gap-3">
				<h2 class="text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
					{m.nav_points()}
				</h2>
				<div class="flex items-center gap-3">
					<label
						class="flex h-9 items-center gap-2 rounded-lg border px-3"
						style="background: var(--color-surface-2); border-color: var(--color-border);"
					>
						<span class="shrink-0 text-xs text-[var(--color-text-muted)]"
							>{m.points_per_page()}</span
						>
						<select
							bind:value={pageSize}
							onchange={resetPaging}
							class="noborder bg-transparent text-sm outline-none"
						>
							{#each PAGE_SIZE_OPTIONS as n (n)}
								<option value={n}>{n}</option>
							{/each}
						</select>
					</label>
					{#if isAdmin}
						<Button variant="secondary" size="sm" onclick={handleExport} disabled={exporting}>
							<Download size={14} />
							{exporting ? m.points_exporting() : m.points_export_all()}
						</Button>
					{/if}
				</div>
			</div>
			{#if exportError}
				<div
					class="mb-3 rounded-lg px-3 py-2 text-xs"
					style="background: var(--color-danger-soft); color: var(--color-danger);"
				>
					{exportError}
				</div>
			{/if}

			<!-- Filters (applied server-side, so each page stays full) -->
			<div
				class="mb-3 grid grid-cols-1 gap-3 rounded-xl border p-4 sm:grid-cols-2 lg:grid-cols-4"
				style="background: var(--color-surface-2); border-color: var(--color-border);"
			>
				<label
					class="flex h-10 items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)]"
					style="background: var(--color-surface-2); border-color: var(--color-border);"
				>
					<span class="shrink-0 text-xs text-[var(--color-text-muted)]"
						>{m.ingest_col_source_type()}</span
					>
					<select
						bind:value={filterSourceType}
						onchange={resetPaging}
						class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none"
					>
						<option value="">{m.points_all()}</option>
						{#each $facets.data?.source_types ?? [] as st (st)}
							<option value={st}>{st}</option>
						{/each}
					</select>
				</label>
				<label
					class="flex h-10 items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)]"
					style="background: var(--color-surface-2); border-color: var(--color-border);"
				>
					<span class="shrink-0 text-xs text-[var(--color-text-muted)]"
						>{m.directives_field_language()}</span
					>
					<select
						bind:value={filterLanguage}
						onchange={resetPaging}
						class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none"
					>
						<option value="">{m.points_all()}</option>
						<option value="bg">bg</option>
						<option value="en">en</option>
					</select>
				</label>
				<label
					class="col-span-2 flex h-10 items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)]"
					style="background: var(--color-surface-2); border-color: var(--color-border);"
				>
					<span class="shrink-0 text-xs text-[var(--color-text-muted)]"
						>{m.point_field_title()}</span
					>
					<select
						bind:value={filterTitle}
						onchange={resetPaging}
						class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none"
					>
						<option value="">{m.points_all()}</option>
						{#each $facets.data?.titles ?? [] as t (t)}
							<option value={t}>{t}</option>
						{/each}
					</select>
				</label>
				{#if hasActiveFilters}
					<div class="col-span-2 flex justify-end lg:col-span-4">
						<button
							onclick={clearFilters}
							class="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-3)] hover:text-[var(--color-text)]"
							title={m.points_clear_filters()}
						>
							<X size={13} />
							{m.points_clear_filters()}
						</button>
					</div>
				{/if}
			</div>

			<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
				<div class="overflow-x-auto">
					<table class="data-table data-table-stack">
						<thead>
							<tr>
								<th>{m.points_col_id()}</th>
								<th>{m.point_field_title()}</th>
								<th>URL</th>
								<th>{m.ingest_col_source_type()}</th>
								<th>{m.common_reference_year()}</th>
								<th>{m.common_lang()}</th>
								{#if isAdmin}<th></th>{/if}
							</tr>
						</thead>
						<tbody>
							{#if $points.isPending}
								<tr>
									<td colspan="7" class="py-12 text-center text-sm text-[var(--color-text-muted)]">
										{m.points_loading()}
									</td>
								</tr>
							{:else if $points.isError}
								<tr>
									<td
										colspan="7"
										class="py-12 text-center text-sm"
										style="color: var(--color-danger);"
									>
										{m.points_load_failed()}
									</td>
								</tr>
							{:else if ($points.data?.items ?? []).length}
								{#each $points.data?.items ?? [] as point (point.id)}
									<tr>
										<td data-label={m.points_col_id()}>
											<a
												href="/points/{point.id}"
												class="font-mono text-xs hover:underline"
												style="color: var(--color-brand);"
											>
												{point.id.slice(0, 8)}…
											</a>
										</td>
										<td data-label={m.point_field_title()} class="stack-block max-w-xs">
											<span class="line-clamp-2 text-sm">
												{(point.payload.title as string) ?? '—'}
											</span>
										</td>
										<td data-label="URL" class="stack-block max-w-xs">
											{#if point.payload.url}
												<a
													href={point.payload.url as string}
													target="_blank"
													rel="noopener noreferrer"
													class="block truncate text-xs hover:underline"
													style="color: var(--color-brand);"
													title={point.payload.url as string}
												>
													{point.payload.url as string}
												</a>
											{:else}
												<span class="text-[var(--color-text-muted)]">—</span>
											{/if}
										</td>
										<td data-label={m.ingest_col_source_type()}
											>{(point.payload.source_type as string) ?? '—'}</td
										>
										<td data-label={m.common_reference_year()} class="tabular-nums"
											>{(point.payload.legal_year as string | number) ?? '—'}</td
										>
										<td data-label={m.common_lang()}>{(point.payload.language as string) ?? '—'}</td
										>
										{#if isAdmin}
											<td data-label="" class="w-16 text-right">
												<button
													onclick={() => $deletePoint.mutate(point.id)}
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-danger)]"
													title={m.point_delete_btn()}
													aria-label={m.point_delete_btn()}
												>
													<Trash2 size={14} />
												</button>
											</td>
										{/if}
									</tr>
								{/each}
							{:else}
								<tr>
									<td colspan="7" class="py-12 text-center">
										<Database size={28} class="mx-auto text-[var(--color-text-muted)]" />
										<p class="mt-2 text-sm text-[var(--color-text-muted)]">
											{hasActiveFilters ? m.points_none_match() : m.points_empty()}
										</p>
									</td>
								</tr>
							{/if}
						</tbody>
					</table>
				</div>
			</div>
		</div>

		<!-- Cursor pagination -->
		{#if $points.data}
			<div class="flex items-center justify-between">
				<Button
					variant="secondary"
					size="sm"
					onclick={() => (cursor = null)}
					disabled={cursor === null}
				>
					{m.points_first_page()}
				</Button>
				{#if $points.data.next_offset_id}
					<Button
						variant="secondary"
						size="sm"
						onclick={() => (cursor = $points.data!.next_offset_id)}
					>
						{m.points_next_page()}
					</Button>
				{/if}
			</div>
		{/if}
	{/if}
</div>
