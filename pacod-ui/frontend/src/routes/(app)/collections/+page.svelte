<script lang="ts">
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import {
		Trash2,
		AlertTriangle,
		X,
		Camera,
		Download,
		Upload,
		RotateCcw,
		CheckCircle2,
		Star
	} from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		createCollectionInfoQuery,
		createCollectionsQuery,
		activeCollectionQuery,
		setActiveCollectionMutation,
		createSnapshotsQuery,
		createSnapshotMutation,
		deleteSnapshotMutation,
		recoverSnapshotMutation,
		uploadSnapshotMutation,
		deleteCollectionMutation,
		snapshotDownloadUrl,
		type CollectionSummary
	} from '$lib/features/knowledge/queries';
	import { hasRole } from '$lib/types/auth';
	import { m } from '$lib/paraglide/messages';

	let { data } = $props();

	const isAdmin = $derived(hasRole(data.user, 'admin'));
	const pid = $derived(selectedProfile.id);
	const info = $derived(createCollectionInfoQuery(pid));
	const collections = $derived(createCollectionsQuery(pid));
	const active = $derived(activeCollectionQuery(pid));

	// ---- Active-collection selection --------------------------------
	const setActive = $derived(setActiveCollectionMutation(pid));
	let selectMessage = $state('');
	let selectError = $state('');

	async function handleSetActive(c: CollectionSummary) {
		selectMessage = '';
		selectError = '';
		if (c.is_active) return;
		if (!confirm(m.collections_confirm_set_active({ name: c.name }))) return;
		try {
			const state = await $setActive.mutateAsync(c.name);
			selectMessage = m.collections_now_active({ name: state.active });
		} catch (e) {
			selectError = e instanceof Error ? e.message : m.collections_set_active_failed();
		}
	}

	async function handleRevertDefault() {
		selectMessage = '';
		selectError = '';
		if (!confirm(m.collections_confirm_revert())) return;
		try {
			const state = await $setActive.mutateAsync(null);
			selectMessage = m.collections_reverted({ name: state.active });
		} catch (e) {
			selectError = e instanceof Error ? e.message : m.collections_revert_failed();
		}
	}

	function fmtDim(c: CollectionSummary): string {
		return c.vector_size ? `${c.vector_size}d · ${c.distance ?? ''}`.trim() : '—';
	}

	// ---- Snapshots & collection lifecycle ---------------------------
	const snapshots = $derived(createSnapshotsQuery(pid));
	const createSnap = $derived(createSnapshotMutation(pid));
	const deleteSnap = $derived(deleteSnapshotMutation(pid));
	const recoverSnap = $derived(recoverSnapshotMutation(pid));
	const uploadSnap = $derived(uploadSnapshotMutation(pid));
	const deleteCol = $derived(deleteCollectionMutation(pid));

	let snapMessage = $state('');
	let snapError = $state('');
	let confirmDeleteSnap = $state<string | null>(null); // composite key "collection::name"
	let snapFile = $state<File | null>(null);
	let snapFileInput = $state<HTMLInputElement | null>(null);
	let uploadTarget = $state('');
	let deleteColConfirm = $state('');

	// The active collection is the snapshot/lifecycle target (server-side).
	const mainCollection = $derived($active.data?.active ?? $info.data?.name ?? '');
	const parentsCollection = $derived(mainCollection ? `${mainCollection}_parents` : '');

	// A profile switch swaps the whole collection family. Selections that name a
	// collection (upload target, type-to-confirm) and the per-action messages
	// belong to the previous profile — an upload with a stale `_parents` target
	// would be rejected by the server, and a stale confirmation would be wrong.
	$effect(() => {
		void pid;
		uploadTarget = '';
		deleteColConfirm = '';
		confirmDeleteSnap = null;
		selectMessage = '';
		selectError = '';
		snapMessage = '';
		snapError = '';
	});

	function fmtBytes(n: number | null | undefined): string {
		if (n === null || n === undefined) return '—';
		const u = ['B', 'KB', 'MB', 'GB'];
		let v = n,
			i = 0;
		while (v >= 1024 && i < u.length - 1) {
			v /= 1024;
			i++;
		}
		return `${v.toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
	}

	function fmtDate(s: string | null | undefined): string {
		return s ? new Date(s).toLocaleString() : '—';
	}

	async function handleCreateSnapshot() {
		snapMessage = '';
		snapError = '';
		try {
			const snaps = await $createSnap.mutateAsync();
			snapMessage = m.collections_snap_created({
				n: snaps.length,
				names: snaps.map((s) => s.collection).join(', ')
			});
		} catch (e) {
			snapError = e instanceof Error ? e.message : m.collections_create_snap_failed();
		}
	}

	async function handleRecover(name: string, collection: string) {
		snapMessage = '';
		snapError = '';
		if (!confirm(m.collections_confirm_recover({ collection, name }))) return;
		try {
			await $recoverSnap.mutateAsync({ name, collection });
			snapMessage = m.collections_restored({ collection, name });
		} catch (e) {
			snapError = e instanceof Error ? e.message : m.collections_recover_failed();
		}
	}

	async function handleDeleteSnapshot(name: string, collection: string) {
		snapMessage = '';
		snapError = '';
		try {
			await $deleteSnap.mutateAsync({ name, collection });
			confirmDeleteSnap = null;
			snapMessage = m.collections_snap_deleted({ name });
		} catch (e) {
			snapError = e instanceof Error ? e.message : m.collections_delete_failed();
		}
	}

	async function handleUpload() {
		snapMessage = '';
		snapError = '';
		if (!snapFile) return;
		const target = uploadTarget || mainCollection;
		if (!confirm(m.collections_confirm_import({ file: snapFile.name, target }))) return;
		try {
			await $uploadSnap.mutateAsync({ file: snapFile, collection: target });
			snapMessage = m.collections_imported({ file: snapFile.name, target });
			snapFile = null;
			if (snapFileInput) snapFileInput.value = '';
		} catch (e) {
			snapError = e instanceof Error ? e.message : m.collections_upload_failed();
		}
	}

	async function handleDeleteCollection() {
		snapMessage = '';
		snapError = '';
		const name = mainCollection;
		if (deleteColConfirm !== name) {
			snapError = m.collections_type_to_confirm({ name });
			return;
		}
		if (!confirm(m.collections_confirm_delete_collection({ name }))) return;
		try {
			await $deleteCol.mutateAsync(name);
			snapMessage = m.collections_collection_deleted({ name });
			deleteColConfirm = '';
		} catch (e) {
			snapError = e instanceof Error ? e.message : m.collections_delete_collection_failed();
		}
	}
</script>

<svelte:head><title>{m.collections_title()}</title></svelte:head>

<div class="space-y-6">
	<!-- Header -->
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">{m.nav_collections()}</h1>
		<p class="mt-1 text-sm text-[var(--color-text-muted)]">
			{m.collections_intro()}
		</p>
	</div>

	{#if !isAdmin}
		<div
			class="rounded-xl border px-5 py-4 text-sm"
			style="border-color: var(--color-border); color: var(--color-text-muted);"
		>
			{m.collections_admin_required()}
		</div>
	{:else}
		<!-- Active-collection summary -->
		<div
			class="rounded-xl border p-5"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<div class="flex flex-wrap items-center justify-between gap-3">
				<div class="flex items-center gap-3">
					<div
						class="flex h-9 w-9 items-center justify-center rounded-lg"
						style="background: var(--color-brand-soft);"
					>
						<Star size={16} style="color: var(--color-brand);" />
					</div>
					<div>
						<div class="text-xs font-medium tracking-wide text-[var(--color-text-muted)] uppercase">
							{m.collections_active_collection()}
						</div>
						<div class="flex items-center gap-2">
							<span class="font-mono text-sm font-semibold">{$active.data?.active ?? '…'}</span>
							{#if $active.data?.is_override}
								<span
									class="rounded-full px-2 py-0.5 text-[10px] font-medium"
									style="background: var(--color-warning-soft); color: var(--color-warning);"
									>{m.collections_override()}</span
								>
							{:else}
								<span
									class="rounded-full px-2 py-0.5 text-[10px] font-medium"
									style="background: var(--color-surface-3); color: var(--color-text-muted);"
									>{m.agent_prompts_status_default()}</span
								>
							{/if}
						</div>
					</div>
				</div>
				{#if $active.data?.is_override}
					<Button
						variant="secondary"
						size="sm"
						onclick={handleRevertDefault}
						disabled={$setActive.isPending}
					>
						<RotateCcw size={14} />
						{m.collections_revert_default({ name: $active.data.default })}
					</Button>
				{/if}
			</div>

			{#if selectMessage}
				<div
					class="mt-3 rounded-lg px-3 py-2 text-xs"
					style="background: var(--color-success-soft); color: var(--color-success);"
				>
					{selectMessage}
				</div>
			{/if}
			{#if selectError}
				<div
					class="mt-3 rounded-lg px-3 py-2 text-xs"
					style="background: var(--color-danger-soft); color: var(--color-danger);"
				>
					{selectError}
				</div>
			{/if}
		</div>

		<!-- Collections list -->
		<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
			<div class="overflow-x-auto">
				<table class="data-table data-table-stack">
					<thead>
						<tr>
							<th>{m.collections_col_collection()}</th>
							<th>{m.collections_col_points()}</th>
							<th>{m.collections_col_vectors()}</th>
							<th>{m.common_status()}</th>
							<th class="text-right">{m.common_active_cap()}</th>
						</tr>
					</thead>
					<tbody>
						{#if $collections.isPending}
							<tr
								><td colspan="5" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
									>{m.collections_loading()}</td
								></tr
							>
						{:else if $collections.isError}
							<tr
								><td
									colspan="5"
									class="py-12 text-center text-sm"
									style="color: var(--color-danger);">{m.collections_load_failed()}</td
								></tr
							>
						{:else if ($collections.data?.length ?? 0) > 0}
							{#each $collections.data ?? [] as c (c.name)}
								<tr style={c.is_active ? 'background: var(--color-brand-soft);' : ''}>
									<td data-label={m.collections_col_collection()}>
										<div class="flex flex-wrap items-center justify-end gap-2 sm:justify-start">
											<span class="font-mono text-sm font-medium">{c.name}</span>
											{#if c.is_default}
												<span
													class="rounded-full px-2 py-0.5 text-[10px] font-medium"
													style="background: var(--color-surface-3); color: var(--color-text-muted);"
													>{m.agent_prompts_status_default()}</span
												>
											{/if}
											{#each c.aliases as a (a)}
												<span
													class="rounded-full px-2 py-0.5 text-[10px] font-medium"
													style="background: var(--color-brand-soft); color: var(--color-brand);"
													title="alias">↳ {a}</span
												>
											{/each}
										</div>
									</td>
									<td data-label={m.collections_col_points()} class="tabular-nums"
										>{c.points_count.toLocaleString()}</td
									>
									<td
										data-label={m.collections_col_vectors()}
										class="text-xs text-[var(--color-text-muted)]">{fmtDim(c)}</td
									>
									<td data-label={m.common_status()}>
										<span
											class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium"
											style={c.status === 'green'
												? 'background: var(--color-success-soft); color: var(--color-success);'
												: 'background: var(--color-warning-soft); color: var(--color-warning);'}
										>
											{c.status}
										</span>
									</td>
									<td data-label="" class="w-40 text-right">
										{#if c.is_active}
											<span
												class="inline-flex items-center gap-1.5 text-xs font-semibold"
												style="color: var(--color-brand);"
											>
												<CheckCircle2 size={14} />
												{m.common_active_cap()}
											</span>
										{:else}
											<Button
												variant="secondary"
												size="sm"
												onclick={() => handleSetActive(c)}
												disabled={$setActive.isPending}
											>
												{m.collections_set_active()}
											</Button>
										{/if}
									</td>
								</tr>
							{/each}
						{:else}
							<tr
								><td colspan="5" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
									>{m.collections_empty()}</td
								></tr
							>
						{/if}
					</tbody>
				</table>
			</div>
		</div>

		<!-- ============================================================ -->
		<!-- SNAPSHOTS & LIFECYCLE                                         -->
		<!-- ============================================================ -->
		<div
			class="rounded-xl border p-5"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<div class="mb-1 flex items-center gap-2">
				<Camera size={15} style="color: var(--color-brand);" />
				<h2 class="text-sm font-semibold">{m.collections_snapshots_lifecycle()}</h2>
				<span class="text-xs text-[var(--color-text-muted)]"
					>{m.collections_targets()} <span class="font-mono">{mainCollection || '…'}</span></span
				>
			</div>
			<p class="mb-4 text-xs text-[var(--color-text-muted)]">
				{m.collections_snapshots_intro()}
			</p>

			<!-- Actions row -->
			<div class="mb-4 flex flex-wrap items-center gap-3">
				<Button
					variant="primary"
					size="sm"
					onclick={handleCreateSnapshot}
					disabled={$createSnap.isPending}
				>
					<Camera size={14} />
					{$createSnap.isPending ? m.collections_creating() : m.collections_create_snapshot()}
				</Button>

				<span class="mx-1 h-6 w-px" style="background: var(--color-border);"></span>

				<label
					class="flex h-9 w-full cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm sm:w-auto"
					style="background: var(--color-surface); border-color: var(--color-border);"
				>
					<Upload size={14} class="text-[var(--color-text-muted)]" />
					<span class="truncate text-[var(--color-text-muted)]"
						>{snapFile ? snapFile.name : m.collections_choose_file()}</span
					>
					<input
						bind:this={snapFileInput}
						type="file"
						accept=".snapshot"
						class="hidden"
						onchange={(e) => (snapFile = (e.currentTarget as HTMLInputElement).files?.[0] ?? null)}
					/>
				</label>
				<select
					bind:value={uploadTarget}
					class="h-9 w-full rounded-lg border px-2 text-sm sm:w-auto"
					style="background: var(--color-surface); border-color: var(--color-border);"
					title={m.collections_target_collection()}
				>
					<option value="">{m.collections_target_main({ name: mainCollection || 'main' })}</option>
					<option value={parentsCollection}
						>{m.collections_target_parents({ name: parentsCollection || '_parents' })}</option
					>
				</select>
				<Button
					variant="secondary"
					size="sm"
					onclick={handleUpload}
					disabled={!snapFile || $uploadSnap.isPending}
				>
					{$uploadSnap.isPending ? m.collections_importing() : m.collections_import()}
				</Button>
			</div>

			{#if snapMessage}
				<div
					class="mb-3 rounded-lg px-3 py-2 text-xs"
					style="background: var(--color-success-soft); color: var(--color-success);"
				>
					{snapMessage}
				</div>
			{/if}
			{#if snapError}
				<div
					class="mb-3 rounded-lg px-3 py-2 text-xs"
					style="background: var(--color-danger-soft); color: var(--color-danger);"
				>
					{snapError}
				</div>
			{/if}

			<!-- Snapshot list -->
			<div class="overflow-hidden rounded-lg border" style="border-color: var(--color-border);">
				<table class="data-table data-table-stack">
					<thead>
						<tr
							><th>{m.collections_col_snapshot()}</th><th>{m.collections_col_collection()}</th><th
								>{m.collections_col_size()}</th
							><th>{m.collections_col_created()}</th><th class="text-right"
								>{m.model_prices_col_actions()}</th
							></tr
						>
					</thead>
					<tbody>
						{#if $snapshots.isPending}
							<tr
								><td colspan="5" class="py-6 text-center text-xs text-[var(--color-text-muted)]"
									>{m.collections_loading_snapshots()}</td
								></tr
							>
						{:else if $snapshots.isError}
							<tr
								><td
									colspan="5"
									class="py-6 text-center text-xs"
									style="color: var(--color-danger);">{m.collections_load_snapshots_failed()}</td
								></tr
							>
						{:else if ($snapshots.data?.length ?? 0) > 0}
							{#each $snapshots.data ?? [] as snap (snap.collection + '::' + snap.name)}
								<tr>
									<td data-label={m.collections_col_snapshot()} class="stack-block max-w-xs"
										><span class="line-clamp-1 font-mono text-xs" title={snap.name}
											>{snap.name}</span
										></td
									>
									<td data-label={m.collections_col_collection()}>
										<span
											class="rounded-full px-2 py-0.5 text-[10px] font-medium"
											style="background: var(--color-brand-soft); color: var(--color-brand);"
										>
											{snap.collection === mainCollection
												? m.collections_snap_main()
												: m.collections_snap_parents()}
										</span>
									</td>
									<td
										data-label={m.collections_col_size()}
										class="text-[var(--color-text-muted)] tabular-nums">{fmtBytes(snap.size)}</td
									>
									<td
										data-label={m.collections_col_created()}
										class="text-xs whitespace-nowrap text-[var(--color-text-muted)]"
										>{fmtDate(snap.creation_time)}</td
									>
									<td data-label="" class="w-44 text-right">
										{#if confirmDeleteSnap === snap.collection + '::' + snap.name}
											<div class="flex items-center justify-end gap-2">
												<span class="text-xs text-[var(--color-text-muted)]"
													>{m.collections_delete_q()}</span
												>
												<button
													onclick={() => handleDeleteSnapshot(snap.name, snap.collection)}
													class="touch-target rounded-lg p-1.5"
													style="color: var(--color-danger);"
													aria-label={m.common_confirm_delete()}
													title={m.common_confirm()}><Trash2 size={14} /></button
												>
												<button
													onclick={() => (confirmDeleteSnap = null)}
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-3)]"
													aria-label={m.common_cancel()}
													title={m.common_cancel()}><X size={14} /></button
												>
											</div>
										{:else}
											<div class="flex items-center justify-end gap-1">
												<button
													onclick={() => handleRecover(snap.name, snap.collection)}
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-brand)]"
													title={m.collections_recover_title()}
													aria-label={m.collections_recover_title()}><RotateCcw size={14} /></button
												>
												<a
													href={snapshotDownloadUrl(snap.name, snap.collection, pid)}
													download
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-brand)]"
													title={m.collections_download_title()}
													aria-label={m.collections_download_title()}><Download size={14} /></a
												>
												<button
													onclick={() => (confirmDeleteSnap = snap.collection + '::' + snap.name)}
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-danger)]"
													title={m.collections_delete_snapshot_title()}
													aria-label={m.common_delete()}><Trash2 size={14} /></button
												>
											</div>
										{/if}
									</td>
								</tr>
							{/each}
						{:else}
							<tr
								><td colspan="5" class="py-6 text-center text-xs text-[var(--color-text-muted)]"
									>{m.collections_no_snapshots()}</td
								></tr
							>
						{/if}
					</tbody>
				</table>
			</div>

			<!-- Danger zone: delete collection -->
			<div
				class="mt-4 rounded-lg border p-4"
				style="border-color: var(--color-danger); background: var(--color-danger-soft);"
			>
				<div class="mb-2 flex items-center gap-2">
					<AlertTriangle size={14} style="color: var(--color-danger);" />
					<h3 class="text-xs font-semibold" style="color: var(--color-danger);">
						{m.collections_delete_collection_title()}
					</h3>
				</div>
				<p class="mb-3 text-xs text-[var(--color-text-muted)]">
					{m.collections_danger_intro({ name: mainCollection || m.collections_the_collection() })}
				</p>
				<div class="flex flex-col gap-3 sm:flex-row sm:items-center">
					<input
						type="text"
						placeholder={mainCollection || m.collections_collection_name()}
						bind:value={deleteColConfirm}
						class="field-input w-full sm:w-64"
					/>
					<Button
						variant="danger"
						size="sm"
						onclick={handleDeleteCollection}
						disabled={$deleteCol.isPending || deleteColConfirm !== mainCollection}
						class="w-full sm:w-auto"
					>
						<Trash2 size={14} />
						{$deleteCol.isPending ? m.collections_deleting() : m.collections_delete_collection()}
					</Button>
				</div>
			</div>
		</div>
	{/if}
</div>
