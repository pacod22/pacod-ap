<script lang="ts">
	import { Check, X, ThumbsDown, FileText, RefreshCw, AlertCircle, Pencil } from 'lucide-svelte';
	import { useQueryClient } from '@tanstack/svelte-query';
	import Button from '$lib/components/ui/Button.svelte';
	import FindingDetail from '$lib/components/FindingDetail.svelte';
	import { createFindingsQuery, loopKeys, type LoopFinding } from '$lib/features/loop/queries';
	import {
		fetchCandidate,
		approveCandidateMutation,
		rejectCandidateMutation,
		updateCandidateMutation,
		type KBCandidate
	} from '$lib/features/kb-candidates/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { m } from '$lib/paraglide/messages';

	let { data } = $props();

	const qc = useQueryClient();
	// Filters: by remediation route (server-side — a client filter over a capped
	// page hides rows), plus a "needs confirmation only" toggle.
	let routeFilter = $state('all');
	let confirmOnly = $state(false);
	// The SSR snapshot is unscoped (default profile), so it only seeds the query
	// for the global selection; a scoped profile must fetch its own rows.
	const findingsQuery = $derived(
		createFindingsQuery(
			selectedProfile.id || routeFilter !== 'all' ? undefined : data.initial,
			8000,
			selectedProfile.id,
			{ route: routeFilter, limit: 200 }
		)
	);

	const allReviews = $derived($findingsQuery.data ?? []);
	const rows = $derived(
		[...allReviews]
			.filter((f: LoopFinding) => !confirmOnly || f.needs_confirmation)
			.sort((a, b) => b.created_at.localeCompare(a.created_at))
	);
	const pendingCount = $derived(allReviews.filter((f) => f.needs_confirmation).length);

	const approveMut = approveCandidateMutation();
	const rejectMut = rejectCandidateMutation();
	const updateMut = updateCandidateMutation();

	// ----- Detail dialog -------------------------------------------------------
	let detailDialog = $state<HTMLDialogElement | null>(null);
	let active = $state<LoopFinding | null>(null);
	let candidate = $state<KBCandidate | null>(null);
	let candidateLoading = $state(false);
	let actionError = $state('');
	let confirmingApprove = $state(false);
	let rejecting = $state(false);
	let rejectNotes = $state('');

	// Reviewer edits. Approval writes the candidate's text straight into the curated
	// tier, so correcting a claim before approving is the difference between
	// "approve as-is or throw it away" and an actual review.
	let editing = $state(false);
	let editForm = $state({ topic: '', claim: '', markdown: '' });

	function startEdit() {
		if (!candidate) return;
		editForm = {
			topic: candidate.topic,
			claim: candidate.claim,
			markdown: candidate.markdown ?? ''
		};
		editing = true;
		confirmingApprove = false;
		rejecting = false;
	}

	async function saveEdit() {
		if (!candidate) return;
		actionError = '';
		try {
			candidate = await $updateMut.mutateAsync({
				id: candidate.id,
				patch: {
					topic: editForm.topic,
					claim: editForm.claim,
					markdown: editForm.markdown || undefined
				}
			});
			editing = false;
		} catch (e: unknown) {
			actionError = e instanceof Error ? e.message : m.kb_save_failed();
		}
	}

	async function openDetail(f: LoopFinding) {
		active = f;
		candidate = null;
		actionError = '';
		confirmingApprove = false;
		rejecting = false;
		editing = false;
		rejectNotes = '';
		detailDialog?.showModal();
		// Pull the synthesised candidate content on demand (markdown/claim/grounding)
		// so an approval is informed — the list row doesn't carry it.
		if (f.candidate_id != null) {
			candidateLoading = true;
			try {
				candidate = await fetchCandidate(f.candidate_id);
			} catch (e: unknown) {
				actionError = e instanceof Error ? e.message : m.kb_load_candidate_failed();
			} finally {
				candidateLoading = false;
			}
		}
	}

	// After a confirm/reject the finding's badge must clear, so invalidate the
	// findings query (the mutations only invalidate the candidate lists).
	async function refreshAfterDecision() {
		await qc.invalidateQueries({ queryKey: loopKeys.findings() });
	}

	async function approve() {
		if (active?.candidate_id == null) return;
		actionError = '';
		try {
			await $approveMut.mutateAsync(active.candidate_id);
			await refreshAfterDecision();
			detailDialog?.close();
		} catch (e: unknown) {
			actionError = e instanceof Error ? e.message : m.kb_approve_failed();
		}
	}

	async function reject() {
		if (active?.candidate_id == null) return;
		actionError = '';
		try {
			await $rejectMut.mutateAsync({
				id: active.candidate_id,
				review_notes: rejectNotes || undefined
			});
			await refreshAfterDecision();
			detailDialog?.close();
		} catch (e: unknown) {
			actionError = e instanceof Error ? e.message : m.kb_reject_failed();
		}
	}

	function assessStyle(a: string | null): string {
		if (a === 'incorrect')
			return 'background: var(--color-danger-soft); color: var(--color-danger);';
		if (a === 'partial') return 'background: var(--color-surface-3); color: var(--color-text);';
		if (a === 'correct') return 'background: var(--color-brand-soft); color: var(--color-brand);';
		return 'background: var(--color-surface-3); color: var(--color-text-muted);';
	}
</script>

<svelte:head><title>{m.kb_title()}</title></svelte:head>

<div class="space-y-6">
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
		<div>
			<h1 class="text-2xl font-semibold tracking-tight">{m.nav_review()}</h1>
			<p class="mt-1 max-w-2xl text-sm text-[var(--color-text-muted)]">
				{m.kb_intro()}
			</p>
		</div>
		<div class="flex items-end gap-3">
			<div>
				<label class="field-label" for="fld-page-kb-candidates-1">{m.kb_route()}</label>
				<select id="fld-page-kb-candidates-1" bind:value={routeFilter} class="field-input">
					<option value="all">{m.kb_route_all()}</option>
					<option value="content_gap">content_gap</option>
					<option value="prompt_fix">prompt_fix</option>
					<option value="retrieval">retrieval</option>
					<option value="golden">golden</option>
					<option value="none">none</option>
				</select>
			</div>
			<label class="flex h-9 cursor-pointer items-center gap-2 text-sm whitespace-nowrap">
				<input type="checkbox" bind:checked={confirmOnly} />
				{m.kb_needs_confirmation_filter()}{pendingCount ? ` (${pendingCount})` : ''}
			</label>
		</div>
	</div>

	<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.kb_col_question()}</th>
						<th>{m.kb_col_route()}</th>
						<th>{m.kb_col_assessment()}</th>
						<th>{m.kb_col_confirmation()}</th>
						<th>{m.kb_col_conf()}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#if $findingsQuery.isPending}
						<tr
							><td colspan="6" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
								>{m.kb_loading()}</td
							></tr
						>
					{:else if $findingsQuery.isError}
						<tr
							><td colspan="6" class="py-12 text-center text-sm" style="color: var(--color-danger);"
								>{m.kb_load_failed()}</td
							></tr
						>
					{:else if rows.length > 0}
						{#each rows as f (f.id)}
							<tr class="cursor-pointer" onclick={() => openDetail(f)}>
								<td
									data-label={m.kb_col_question()}
									class="max-w-[360px] truncate font-medium"
									title={f.question ?? ''}
								>
									{f.question ?? '—'}
								</td>
								<td data-label={m.kb_col_route()} class="text-xs text-[var(--color-text-muted)]"
									>{f.route ?? '—'}</td
								>
								<td data-label={m.kb_col_assessment()}>
									<span
										class="rounded-md px-1.5 py-0.5 text-xs font-medium"
										style={assessStyle(f.assessment)}>{f.assessment ?? '—'}</span
									>
								</td>
								<td data-label={m.kb_col_confirmation()}>
									{#if f.needs_confirmation}
										<span
											class="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium"
											style="background: var(--color-warning-soft, var(--color-danger-soft)); color: var(--color-warning, var(--color-danger));"
										>
											<AlertCircle size={12} />
											{m.kb_needs_confirmation()}
										</span>
									{:else if f.candidate_status}
										<span
											class="rounded-md px-1.5 py-0.5 text-xs font-medium text-[var(--color-text-muted)]"
											style="background: var(--color-surface-3);">{f.candidate_status}</span
										>
									{:else}
										<span class="text-xs text-[var(--color-text-muted)]">—</span>
									{/if}
								</td>
								<td
									data-label={m.kb_col_conf()}
									class="text-xs text-[var(--color-text-muted)] tabular-nums"
									>{f.synthesis_confidence != null ? f.synthesis_confidence.toFixed(2) : '—'}</td
								>
								<td data-label="">
									<button
										class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
										onclick={(e) => {
											e.stopPropagation();
											openDetail(f);
										}}
										aria-label={m.kb_view_review()}
										title={m.kb_view_review()}><FileText size={14} /></button
									>
								</td>
							</tr>
						{/each}
					{:else}
						<tr
							><td colspan="6" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
								>{m.kb_empty()}</td
							></tr
						>
					{/if}
				</tbody>
			</table>
		</div>
	</div>
</div>

<!-- Detail dialog -->
<dialog
	bind:this={detailDialog}
	class="mx-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-2xl overflow-y-auto rounded-2xl p-0 shadow-2xl"
	style="background: var(--color-surface); border: 1px solid var(--color-border);"
>
	<div
		class="flex items-center justify-between border-b px-6 py-4"
		style="border-color: var(--color-border);"
	>
		<h2 class="text-base font-semibold">{m.kb_review_detail()}</h2>
		<button
			type="button"
			onclick={() => detailDialog?.close()}
			class="rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)]"
			aria-label={m.common_close()}><X size={16} /></button
		>
	</div>
	{#if active}
		<div class="space-y-4 px-6 py-5 text-sm">
			<!-- `actions` on: this is the page a reviewer lives in, and dismissing a
			     false positive here is what stops it resurfacing every night. -->
			<FindingDetail finding={active} actions profileId={selectedProfile.id} />

			{#if active.candidate_id != null}
				<div
					class="rounded-xl border p-4"
					style="border-color: var(--color-border); background: var(--color-surface-2);"
				>
					<div class="mb-2 flex items-center gap-2">
						<h3 class="text-sm font-semibold">{m.kb_knowledge_candidate()}</h3>
						{#if active.needs_confirmation}
							<span
								class="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium"
								style="background: var(--color-warning-soft, var(--color-danger-soft)); color: var(--color-warning, var(--color-danger));"
								><AlertCircle size={12} /> {m.kb_needs_confirmation()}</span
							>
						{:else if active.candidate_status}
							<span
								class="rounded-md px-1.5 py-0.5 text-xs font-medium text-[var(--color-text-muted)]"
								style="background: var(--color-surface-3);">{active.candidate_status}</span
							>
						{/if}
					</div>
					{#if candidateLoading}
						<p class="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
							<RefreshCw size={13} class="animate-spin" />
							{m.kb_loading_candidate()}
						</p>
					{:else if candidate && editing}
						<div class="space-y-3">
							<p class="text-xs text-[var(--color-text-muted)]">{m.kb_edit_note()}</p>
							<div>
								<label class="field-label" for="kb-edit-topic">{m.kb_topic()}</label>
								<input
									id="kb-edit-topic"
									type="text"
									bind:value={editForm.topic}
									maxlength="512"
									class="field-input"
								/>
							</div>
							<div>
								<label class="field-label" for="kb-edit-claim">{m.kb_claim()}</label>
								<textarea
									id="kb-edit-claim"
									bind:value={editForm.claim}
									rows={5}
									class="field-textarea"></textarea>
							</div>
							<div>
								<label class="field-label" for="kb-edit-markdown">{m.kb_rendered_note()}</label>
								<textarea
									id="kb-edit-markdown"
									bind:value={editForm.markdown}
									rows={8}
									class="field-textarea font-mono text-xs"></textarea>
							</div>
							<div class="flex gap-2">
								<Button
									size="sm"
									onclick={saveEdit}
									disabled={$updateMut.isPending ||
										!editForm.topic.trim() ||
										!editForm.claim.trim()}
								>
									<Check size={14} />
									{m.common_save()}
								</Button>
								<Button variant="secondary" size="sm" onclick={() => (editing = false)}
									>{m.common_cancel()}</Button
								>
							</div>
						</div>
					{:else if candidate}
						<div class="space-y-3">
							<div>
								<div class="field-label">{m.kb_topic()}</div>
								<p class="font-medium">{candidate.topic}</p>
							</div>
							<div>
								<div class="field-label">{m.kb_claim()}</div>
								<p class="whitespace-pre-wrap">{candidate.claim}</p>
							</div>
							{#if candidate.grounding_citations.length}
								<div>
									<div class="field-label">{m.kb_grounding()}</div>
									<p class="font-mono text-xs text-[var(--color-text-muted)]">
										{candidate.grounding_citations.join(', ')}
									</p>
								</div>
							{/if}
							<!-- What the reviewer is approving INTO the curated tier: does the
							     cited passage support the claim, and what is at stake if wrong. -->
							<div class="flex flex-wrap gap-2 text-xs">
								<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5"
									>{m.kb_grounding_status()} {candidate.grounding_status}</span
								>
								{#if candidate.entailment}
									<span
										class="rounded-md px-1.5 py-0.5 font-medium"
										style={candidate.entailment === 'contradicted'
											? 'background: var(--color-danger-soft); color: var(--color-danger);'
											: candidate.entailment === 'supported'
												? 'background: var(--color-brand-soft); color: var(--color-brand);'
												: 'background: var(--color-surface-3); color: var(--color-text);'}
										title={candidate.entailment_note ?? ''}
										>{m.kb_entailment()} {candidate.entailment}</span
									>
								{/if}
								<span
									class="rounded-md px-1.5 py-0.5"
									style={candidate.stakes === 'high'
										? 'background: var(--color-danger-soft); color: var(--color-danger);'
										: 'background: var(--color-surface-3);'}
									>{m.kb_stakes()} {candidate.stakes}</span
								>
							</div>
							{#if candidate.entailment_note}
								<p class="text-xs text-[var(--color-text-muted)]">{candidate.entailment_note}</p>
							{/if}
							{#if candidate.markdown}
								<details>
									<summary class="cursor-pointer text-sm text-[var(--color-text-muted)]"
										>{m.kb_rendered_note()}</summary
									>
									<pre
										class="mt-2 max-h-64 overflow-auto rounded-lg p-3 text-xs"
										style="background: var(--color-surface); border: 1px solid var(--color-border); white-space: pre-wrap;">{candidate.markdown}</pre>
								</details>
							{/if}
						</div>
					{/if}

					<!-- Gated on the CANDIDATE's own status, not the finding's derived
					     `needs_confirmation` flag. The flag is computed from a separate
					     query, so a pending candidate whose finding fell outside that
					     window rendered read-only with no controls at all — the
					     candidate was unreviewable through the UI. -->
					{#if candidate && candidate.status === 'pending'}
						<div class="mt-4 space-y-3 border-t pt-4" style="border-color: var(--color-border);">
							{#if confirmingApprove}
								<p class="text-sm">{m.kb_confirm_insert()}</p>
								<div class="flex gap-2">
									<Button size="sm" onclick={approve} disabled={$approveMut.isPending}
										>{m.kb_yes_insert()}</Button
									>
									<Button variant="secondary" size="sm" onclick={() => (confirmingApprove = false)}
										>{m.common_cancel()}</Button
									>
								</div>
							{:else if rejecting}
								<textarea
									bind:value={rejectNotes}
									rows={2}
									placeholder={m.kb_reason_placeholder()}
									class="field-textarea"></textarea>
								<div class="flex gap-2">
									<Button size="sm" onclick={reject} disabled={$rejectMut.isPending}
										>{m.kb_confirm_reject()}</Button
									>
									<Button variant="secondary" size="sm" onclick={() => (rejecting = false)}
										>{m.common_cancel()}</Button
									>
								</div>
							{:else if !editing}
								<div class="flex flex-wrap gap-2">
									<Button
										size="sm"
										onclick={() => {
											confirmingApprove = true;
											rejecting = false;
										}}><Check size={14} /> {m.kb_approve()}</Button
									>
									<Button
										variant="secondary"
										size="sm"
										onclick={() => {
											rejecting = true;
											rejectNotes = '';
											confirmingApprove = false;
										}}><ThumbsDown size={14} /> {m.kb_reject()}</Button
									>
									<Button variant="secondary" size="sm" onclick={startEdit} disabled={!candidate}
										><Pencil size={14} /> {m.kb_edit()}</Button
									>
								</div>
							{/if}
						</div>
					{:else if candidate}
						<p
							class="mt-4 border-t pt-4 text-xs text-[var(--color-text-muted)]"
							style="border-color: var(--color-border);"
						>
							{m.kb_already_decided({ status: candidate.status })}
						</p>
					{/if}
				</div>
			{/if}

			{#if actionError}
				<div
					class="rounded-lg px-3 py-2 text-sm"
					style="background: var(--color-danger-soft); color: var(--color-danger);"
				>
					{actionError}
				</div>
			{/if}
		</div>
	{/if}
	<div class="flex justify-end gap-2 border-t px-6 py-4" style="border-color: var(--color-border);">
		<Button type="button" variant="secondary" size="sm" onclick={() => detailDialog?.close()}
			>{m.common_close()}</Button
		>
	</div>
</dialog>
