<script lang="ts">
	import {
		Search,
		X,
		BookPlus,
		Check,
		Eye,
		Trash2,
		ChevronLeft,
		ChevronRight,
		Microscope,
		RefreshCw,
		Square
	} from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import FindingDetail from '$lib/components/FindingDetail.svelte';
	import GatewayConsoleLink from '$lib/components/gateway/GatewayConsoleLink.svelte';
	import GatewayErrorNotice from '$lib/components/gateway/GatewayErrorNotice.svelte';
	import {
		reviewTurnMutation,
		cancelRunRequestMutation,
		createRunRequestQuery,
		createFindingByTurnQuery,
		isRequestSettled
	} from '$lib/features/loop/queries';
	import {
		createConversationListQuery,
		createTurnUsageQuery,
		setReviewStatusMutation,
		setRatingMutation,
		deleteConversationMutation,
		promoteToKbMutation,
		REVIEW_STATUSES,
		AUDIT_EVENT_TYPES,
		type Conversation,
		type ConversationFilters,
		type ReviewStatus,
		type PromoteInput
	} from '$lib/features/conversations/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { page } from '$app/state';
	import { m } from '$lib/paraglide/messages';

	const PAGE_SIZE = 50;

	// `?user_id=` / `?session_id=` seed the filters, so the analytics activity
	// log can link straight to one chat of one user.
	const initialParams = page.url.searchParams;
	let search = $state('');
	let userFilter = $state(initialParams.get('user_id') ?? '');
	let sessionFilter = $state(initialParams.get('session_id') ?? '');
	let eventTypeFilter = $state(''); // '' = Q&A turns (default), 'all' = every event
	let ratingFilter = $state<'' | '1' | '-1'>('');
	let statusFilter = $state<'' | ReviewStatus>('');
	let offset = $state(0);

	let filters = $derived<ConversationFilters>({
		q: search.trim() || undefined,
		user_id: userFilter.trim() || undefined,
		session_id: sessionFilter.trim() || undefined,
		event_type: eventTypeFilter || undefined,
		rating: ratingFilter === '' ? undefined : (Number(ratingFilter) as 1 | -1),
		review_status: statusFilter || undefined,
		profile_id: selectedProfile.id ?? undefined,
		limit: PAGE_SIZE,
		offset
	});

	const conversations = $derived(createConversationListQuery(filters));
	const rows = $derived($conversations.data ?? []);

	const hasFilters = $derived(
		!!(search || userFilter || sessionFilter || eventTypeFilter || ratingFilter || statusFilter)
	);
	const hasPrev = $derived(offset > 0);
	const hasNext = $derived(rows.length === PAGE_SIZE);

	function resetPage() {
		offset = 0;
	}
	function clearFilters() {
		search = '';
		userFilter = '';
		sessionFilter = '';
		eventTypeFilter = '';
		ratingFilter = '';
		statusFilter = '';
		offset = 0;
	}
	function prevPage() {
		offset = Math.max(0, offset - PAGE_SIZE);
	}
	function nextPage() {
		if (hasNext) offset += PAGE_SIZE;
	}
	function fmtTime(iso: string) {
		return new Date(iso).toLocaleString();
	}

	// Localized review-status labels (the underlying values stay in REVIEW_STATUSES).
	function statusLabel(s: ReviewStatus): string {
		return s === 'in_review'
			? m.conv_status_in_review()
			: s === 'processed'
				? m.conv_status_processed()
				: m.conv_status_new();
	}

	// ----- Review status ---------------------------------------------------
	const reviewMut = setReviewStatusMutation();
	let statusPendingId = $state<number | null>(null);

	async function setStatus(c: Conversation, status: string) {
		if (status === (c.review_status ?? 'new')) return;
		statusPendingId = c.id;
		try {
			await $reviewMut.mutateAsync({ id: c.id, status: status as ReviewStatus });
		} finally {
			statusPendingId = null;
		}
	}

	// ----- Rating (admin change / set / remove) ----------------------------
	const ratingMut = setRatingMutation();
	let ratingPendingId = $state<number | null>(null);

	async function setRating(c: Conversation, value: string) {
		const rating = value === '' ? null : (Number(value) as 1 | -1);
		if (rating === (c.rating ?? null)) return;
		ratingPendingId = c.id;
		try {
			await $ratingMut.mutateAsync({ id: c.id, rating });
		} finally {
			ratingPendingId = null;
		}
	}

	// ----- Delete ----------------------------------------------------------
	const deleteMut = deleteConversationMutation();
	let confirmDeleteId = $state<number | null>(null);

	async function confirmDelete(id: number) {
		try {
			await $deleteMut.mutateAsync(id);
			confirmDeleteId = null;
		} catch {
			// surfaced via mutation state
		}
	}

	// ----- Detail dialog ---------------------------------------------------
	let detailEntry = $state<Conversation | null>(null);
	let detailDialog = $state<HTMLDialogElement | null>(null);
	function openDetail(c: Conversation) {
		detailEntry = c;
		reviewRequestId = null;
		reviewError = '';
		detailDialog?.showModal();
	}
	function closeDetail() {
		detailDialog?.close();
	}

	// ----- Review this turn ------------------------------------------------
	// Sends ONE turn through the full improvement-loop pipeline. Asynchronous:
	// the request is queued and the improvement-loop-worker drains it (poll ≥5s,
	// one at a time), so the UI follows the request row, not the finding.
	const reviewTurnMut = reviewTurnMutation();
	let reviewRequestId = $state<number | null>(null);
	let reviewError = $state('');

	const reviewRequest = $derived(createRunRequestQuery(reviewRequestId));
	const reviewSettled = $derived(isRequestSettled($reviewRequest.data?.status));
	const cancelReviewMut = cancelRunRequestMutation();
	async function stopReview() {
		if (reviewRequestId === null) return;
		reviewError = '';
		try {
			await $cancelReviewMut.mutateAsync(reviewRequestId);
		} catch (e) {
			reviewError = e instanceof Error ? e.message : m.rp_stop_failed();
		}
	}
	// Only fetch the finding once the request settled — `loop_findings` upserts per
	// (profile, turn), so a turn a previous run analysed already has a row and we'd
	// otherwise show a stale verdict the moment the button is clicked.
	const reviewFinding = $derived(createFindingByTurnQuery(detailEntry?.turn_id, reviewSettled));

	// Rejection rows carry no turn_id, and only completed turns are analysable.
	const canReview = $derived(
		!!detailEntry?.turn_id && detailEntry?.event_type === 'request_completed'
	);

	async function reviewTurn() {
		if (!detailEntry?.turn_id) return;
		reviewError = '';
		try {
			const req = await $reviewTurnMut.mutateAsync({
				turnId: detailEntry.turn_id,
				// The row's own profile — an admin in global scope has no selection.
				profileId: detailEntry.profile_id ?? null
			});
			reviewRequestId = req.id;
		} catch (e) {
			reviewError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	// Token/cost breakdown for the open entry's turn (lazy — fetched on open).
	// `$derived` re-creates the query when a different entry is opened (same
	// pattern as the list query); the query is disabled until a turn_id exists.
	const turnUsage = $derived(createTurnUsageQuery(detailEntry?.turn_id));
	function fmtUsd(n: number) {
		return n < 0.01 && n > 0 ? `$${n.toFixed(5)}` : `$${n.toFixed(4)}`;
	}
	function fmtNum(n: number) {
		return n.toLocaleString();
	}

	// ----- Promote dialog --------------------------------------------------
	const promoteMut = promoteToKbMutation();
	let dialog = $state<HTMLDialogElement | null>(null);
	let formError = $state('');
	let promoteDone = $state(false);
	let form = $state<PromoteInput>({ title: '', body: '', legal_year: null, language: 'bg' });

	function detectLanguage(c: Conversation): 'bg' | 'en' {
		const dl = (c.classification?.detected_language as string | undefined)?.toLowerCase();
		return dl === 'en' ? 'en' : 'bg';
	}

	function openPromote(c: Conversation) {
		const q = (c.user_message ?? '').trim();
		const a = (c.response ?? '').trim();
		const lang = detectLanguage(c);
		form = {
			title: q.slice(0, 80),
			body: lang === 'en' ? `Question: ${q}\n\nAnswer: ${a}` : `Въпрос: ${q}\n\nОтговор: ${a}`,
			legal_year: (c.classification?.legal_year as number | undefined) ?? null,
			language: lang,
			source_id: c.turn_id ? `qa_${c.turn_id}` : null,
			// Route the curated Q&A to the conversation's own profile; fall back to
			// the active admin scope, then the backend default.
			profile_id: c.profile_id ?? selectedProfile.id ?? undefined
		};
		formError = '';
		promoteDone = false;
		dialog?.showModal();
	}
	function closeDialog() {
		dialog?.close();
		formError = '';
		promoteDone = false;
	}
	async function submitPromote() {
		formError = '';
		try {
			await $promoteMut.mutateAsync({
				...form,
				legal_year:
					form.legal_year === null || String(form.legal_year) === ''
						? null
						: Number(form.legal_year)
			});
			promoteDone = true;
		} catch (e: unknown) {
			formError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}
</script>

<svelte:head><title>{m.conv_title()}</title></svelte:head>

<div class="space-y-6">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">{m.nav_conversations()}</h1>
		<p class="mt-1 max-w-2xl text-sm text-[var(--color-text-muted)]">
			{m.conv_intro()}
		</p>
	</div>

	<!-- Filters -->
	<div
		class="flex flex-col flex-wrap gap-2 rounded-xl border p-3 sm:flex-row sm:items-center"
		style="background: var(--color-surface-2); border-color: var(--color-border);"
	>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 focus-within:border-[var(--color-brand)] sm:w-64"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.conv_search_ph()}
				bind:value={search}
				oninput={resetPage}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none"
			/>
		</label>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 focus-within:border-[var(--color-brand)] sm:w-44"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.debug_console_filter_user()}
				bind:value={userFilter}
				oninput={resetPage}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none"
			/>
		</label>

		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 focus-within:border-[var(--color-brand)] sm:w-44"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.debug_console_filter_session()}
				bind:value={sessionFilter}
				oninput={resetPage}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none"
			/>
		</label>

		<select
			bind:value={eventTypeFilter}
			onchange={resetPage}
			class="field-input h-10 w-full sm:w-auto"
			aria-label={m.conv_event_type()}
		>
			<option value="">{m.conv_qa_turns()}</option>
			<option value="all">{m.conv_all_events()}</option>
			{#each AUDIT_EVENT_TYPES as et}
				<option value={et}>{et}</option>
			{/each}
		</select>

		<select
			bind:value={ratingFilter}
			onchange={resetPage}
			class="field-input h-10 w-full sm:w-auto"
			aria-label={m.conv_col_rating()}
		>
			<option value="">{m.conv_any_rating()}</option>
			<option value="1">{m.conv_liked()}</option>
			<option value="-1">{m.conv_disliked()}</option>
		</select>

		<select
			bind:value={statusFilter}
			onchange={resetPage}
			class="field-input h-10 w-full sm:w-auto"
			aria-label={m.conv_review_status()}
		>
			<option value="">{m.conv_any_status()}</option>
			{#each REVIEW_STATUSES as s}
				<option value={s}>{statusLabel(s)}</option>
			{/each}
		</select>

		{#if hasFilters}
			<Button variant="ghost" size="sm" onclick={clearFilters}
				><X size={14} />{m.common_clear()}</Button
			>
		{/if}
	</div>

	<!-- Table -->
	<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.debug_console_col_time()}</th>
						<th>{m.debug_console_col_user()}</th>
						<th>{m.conv_col_event()}</th>
						<th>{m.kb_col_question()}</th>
						<th>{m.conv_col_answer()}</th>
						<th>{m.conv_col_rating()}</th>
						<th>{m.common_status()}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#if $conversations.isPending}
						<tr
							><td colspan="8" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
								>{m.common_loading()}</td
							></tr
						>
					{:else if $conversations.isError}
						<tr
							><td colspan="8" class="py-12 text-center text-sm" style="color: var(--color-danger);"
								>{m.conv_load_failed()}</td
							></tr
						>
					{:else if rows.length > 0}
						{#each rows as c (c.id)}
							<tr>
								<td
									data-label={m.debug_console_col_time()}
									class="whitespace-nowrap text-[var(--color-text-muted)] tabular-nums"
									>{fmtTime(c.occurred_at)}</td
								>
								<td
									data-label={m.debug_console_col_user()}
									class="font-mono text-xs text-[var(--color-text-muted)]"
									>{c.user_email ?? c.user_id ?? '—'}</td
								>
								<td data-label={m.conv_col_event()}>
									<span
										class="rounded-md px-1.5 py-0.5 font-mono text-xs"
										style="background: var(--color-surface-3); color: var(--color-text-muted);"
										>{c.event_type}</span
									>
								</td>
								<td
									data-label={m.kb_col_question()}
									class="stack-block max-w-[240px] truncate"
									title={c.user_message ?? ''}>{c.user_message ?? '—'}</td
								>
								<td
									data-label={m.conv_col_answer()}
									class="stack-block max-w-[280px] truncate text-[var(--color-text-muted)]"
									title={c.response ?? ''}>{c.response ?? '—'}</td
								>
								<td data-label={m.conv_col_rating()}>
									{#if c.user_id && c.turn_id}
										<div class="flex items-center gap-1.5">
											<select
												value={c.rating == null ? '' : String(c.rating)}
												onchange={(e) => setRating(c, (e.currentTarget as HTMLSelectElement).value)}
												disabled={ratingPendingId === c.id}
												class="field-input h-8 py-0 text-xs"
												aria-label={m.conv_col_rating()}
											>
												<option value="">{m.conv_rating_none()}</option>
												<option value="1">{m.conv_rating_like()}</option>
												<option value="-1">{m.conv_rating_dislike()}</option>
											</select>
											{#if c.rating != null && c.rating_set_by === 'admin'}
												<span
													class="rounded px-1 py-0.5 text-[10px] font-medium tracking-wide uppercase"
													style="background: var(--color-brand-soft); color: var(--color-brand);"
													title={m.conv_admin_rating_title()}>{m.conv_admin()}</span
												>
											{/if}
										</div>
									{:else}
										<span class="text-[var(--color-text-muted)]">—</span>
									{/if}
								</td>
								<td data-label={m.common_status()}>
									<select
										value={c.review_status ?? 'new'}
										onchange={(e) => setStatus(c, (e.currentTarget as HTMLSelectElement).value)}
										disabled={statusPendingId === c.id}
										class="field-input h-8 py-0 text-xs"
										title={c.reviewed_by
											? m.conv_reviewed_by({ who: c.reviewed_by }) +
												(c.reviewed_at ? ' · ' + fmtTime(c.reviewed_at) : '')
											: m.conv_not_reviewed()}
									>
										{#each REVIEW_STATUSES as s}
											<option value={s}>{statusLabel(s)}</option>
										{/each}
									</select>
								</td>
								<td data-label="" class="w-28">
									{#if confirmDeleteId === c.id}
										<div class="flex items-center justify-end gap-1">
											<button
												onclick={() => confirmDelete(c.id)}
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
												onclick={() => openDetail(c)}
												class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-text)]"
												aria-label={m.conv_view_details()}
												title={m.conv_view_details()}><Eye size={14} /></button
											>
											{#if c.user_message}
												<button
													onclick={() => openPromote(c)}
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-3)] hover:text-[var(--color-brand)]"
													aria-label={m.conv_promote_kb()}
													title={m.conv_promote_kb()}><BookPlus size={14} /></button
												>
											{/if}
											<button
												onclick={() => (confirmDeleteId = c.id)}
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
							><td colspan="8" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
								>{hasFilters ? m.conv_none_match() : m.conv_empty()}</td
							></tr
						>
					{/if}
				</tbody>
			</table>
		</div>
	</div>

	<!-- Pagination -->
	<div class="flex items-center justify-between text-sm text-[var(--color-text-muted)]">
		<span
			>{#if !$conversations.isPending}{m.common_showing_range({
					from: rows.length === 0 ? 0 : offset + 1,
					to: offset + rows.length
				})}{/if}</span
		>
		<div class="flex items-center gap-2">
			<button
				onclick={prevPage}
				disabled={!hasPrev}
				class="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm hover:bg-[var(--color-surface-2)] disabled:pointer-events-none disabled:opacity-40"
				><ChevronLeft size={14} /> {m.common_prev()}</button
			>
			<button
				onclick={nextPage}
				disabled={!hasNext}
				class="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm hover:bg-[var(--color-surface-2)] disabled:pointer-events-none disabled:opacity-40"
				>{m.common_next()} <ChevronRight size={14} /></button
			>
		</div>
	</div>
</div>

<!-- Detail dialog -->
<dialog
	bind:this={detailDialog}
	class="mx-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-2xl overflow-y-auto rounded-2xl p-0 shadow-2xl"
	style="background: var(--color-surface); border: 1px solid var(--color-border);"
>
	{#if detailEntry}
		<div
			class="flex items-center justify-between border-b px-6 py-4"
			style="border-color: var(--color-border);"
		>
			<h2 class="text-base font-semibold">{m.conv_entry({ id: detailEntry.id })}</h2>
			<button
				onclick={closeDetail}
				class="rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
				aria-label={m.common_close()}><X size={16} /></button
			>
		</div>

		<div class="space-y-4 overflow-y-auto px-6 py-5" style="max-height: 70vh;">
			<dl class="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
				<div>
					<dt class="text-[var(--color-text-muted)]">{m.conv_occurred_at()}</dt>
					<dd class="mt-0.5 text-[var(--color-text-muted)] tabular-nums">
						{fmtTime(detailEntry.occurred_at)}
					</dd>
				</div>
				<div>
					<dt class="text-[var(--color-text-muted)]">{m.conv_event_type()}</dt>
					<dd class="mt-0.5 font-mono text-xs text-[var(--color-text-muted)]">
						{detailEntry.event_type}
					</dd>
				</div>
				<div>
					<dt class="text-[var(--color-text-muted)]">{m.conv_user_id()}</dt>
					<dd class="mt-0.5 font-mono text-xs text-[var(--color-text-muted)]">
						{detailEntry.user_email ?? detailEntry.user_id ?? '—'}
					</dd>
				</div>
				<div>
					<dt class="text-[var(--color-text-muted)]">{m.conv_session_id()}</dt>
					<dd class="mt-0.5 font-mono text-xs break-all text-[var(--color-text-muted)]">
						{detailEntry.session_id ?? '—'}
					</dd>
				</div>
				<div>
					<dt class="text-[var(--color-text-muted)]">{m.conv_turn_id()}</dt>
					<dd class="mt-0.5 font-mono text-xs text-[var(--color-text-muted)]">
						{detailEntry.turn_id ?? '—'}
					</dd>
				</div>
				<div>
					<dt class="text-[var(--color-text-muted)]">{m.conv_review_status()}</dt>
					<dd class="mt-0.5 text-xs text-[var(--color-text-muted)]">
						{statusLabel((detailEntry.review_status as ReviewStatus) ?? 'new')}
						{#if detailEntry.reviewed_by}<span class="opacity-70">
								· {detailEntry.reviewed_by}</span
							>{/if}
					</dd>
				</div>
			</dl>

			{#if detailEntry.user_message}
				<div>
					<p class="mb-1 text-xs font-medium text-[var(--color-text-muted)]">
						{m.conv_user_message()}
					</p>
					<div
						class="rounded-lg p-3 text-sm text-[var(--color-text-muted)]"
						style="background: var(--color-surface-2); border: 1px solid var(--color-border);"
					>
						{detailEntry.user_message}
					</div>
				</div>
			{/if}
			{#if detailEntry.response}
				<div>
					<p class="mb-1 text-xs font-medium text-[var(--color-text-muted)]">{m.conv_response()}</p>
					<div
						class="max-h-40 overflow-y-auto rounded-lg p-3 text-sm text-[var(--color-text-muted)]"
						style="background: var(--color-surface-2); border: 1px solid var(--color-border);"
					>
						{detailEntry.response}
					</div>
				</div>
			{/if}
			{#if detailEntry.turn_id}
				<div>
					<div class="mb-1 flex flex-wrap items-baseline justify-between gap-x-3">
						<p class="text-xs font-medium text-[var(--color-text-muted)]">
							{m.conv_tokens_cost()}
						</p>
						<!-- The turn's trace in the gateway console (new tab; it refuses framing). -->
						<GatewayConsoleLink href={$turnUsage.data?.console_url} />
					</div>
					<GatewayErrorNotice error={$turnUsage.data?.gateway_error} class="mb-1" />
					{#if $turnUsage.isLoading}
						<p class="text-xs text-[var(--color-text-muted)]">{m.conv_loading_usage()}</p>
					{:else if $turnUsage.isError}
						<p class="text-xs text-[var(--color-danger)]">{m.conv_load_usage_failed()}</p>
					{:else if ($turnUsage.data?.rows.length ?? 0) === 0}
						<!-- With a gateway error, "no usage" would be wrong: the notice above says why. -->
						{#if !$turnUsage.data?.gateway_error}
							<p class="text-xs text-[var(--color-text-muted)]">{m.conv_no_usage()}</p>
						{/if}
					{:else if $turnUsage.data}
						<div class="overflow-x-auto rounded-lg" style="border: 1px solid var(--color-border);">
							<table class="w-full text-xs">
								<thead>
									<tr
										class="text-left text-[var(--color-text-muted)]"
										style="background: var(--color-surface-2);"
									>
										<th class="px-2 py-1.5 font-medium">{m.agent_prompts_col_phase()}</th>
										<th class="px-2 py-1.5 font-medium">{m.model_prices_col_model()}</th>
										<th class="px-2 py-1.5 text-right font-medium">{m.conv_col_input()}</th>
										<th class="px-2 py-1.5 text-right font-medium">{m.conv_col_output()}</th>
										<th class="px-2 py-1.5 text-right font-medium">{m.conv_col_cached()}</th>
										<th class="px-2 py-1.5 text-right font-medium">{m.conv_col_cost()}</th>
									</tr>
								</thead>
								<tbody>
									{#each $turnUsage.data.rows as r (`${r.purpose}:${r.model}`)}
										<tr style="border-top: 1px solid var(--color-border);">
											<td class="px-2 py-1.5 text-[var(--color-text)]">{r.purpose}</td>
											<td class="px-2 py-1.5 font-mono text-[var(--color-text-muted)]">{r.model}</td
											>
											<td class="px-2 py-1.5 text-right text-[var(--color-text-muted)] tabular-nums"
												>{fmtNum(r.input_tokens)}</td
											>
											<td class="px-2 py-1.5 text-right text-[var(--color-text-muted)] tabular-nums"
												>{fmtNum(r.output_tokens)}</td
											>
											<td class="px-2 py-1.5 text-right text-[var(--color-text-muted)] tabular-nums"
												>{r.cached_tokens ? fmtNum(r.cached_tokens) : '—'}</td
											>
											<td class="px-2 py-1.5 text-right text-[var(--color-text-muted)] tabular-nums"
												>{fmtUsd(r.cost_usd)}</td
											>
										</tr>
									{/each}
								</tbody>
								<tfoot>
									<tr
										class="font-medium text-[var(--color-text)]"
										style="border-top: 1px solid var(--color-border); background: var(--color-surface-2);"
									>
										<td class="px-2 py-1.5" colspan="2">{m.conv_total()}</td>
										<td class="px-2 py-1.5 text-right tabular-nums"
											>{fmtNum($turnUsage.data.total.input_tokens)}</td
										>
										<td class="px-2 py-1.5 text-right tabular-nums"
											>{fmtNum($turnUsage.data.total.output_tokens)}</td
										>
										<td class="px-2 py-1.5 text-right tabular-nums"
											>{$turnUsage.data.total.cached_tokens
												? fmtNum($turnUsage.data.total.cached_tokens)
												: '—'}</td
										>
										<td class="px-2 py-1.5 text-right tabular-nums"
											>{fmtUsd($turnUsage.data.total.cost_usd)}</td
										>
									</tr>
								</tfoot>
							</table>
						</div>
						<p class="mt-1 text-[11px] text-[var(--color-text-muted)]">{m.conv_cost_note()}</p>
					{/if}
				</div>
			{/if}
			{#if detailEntry.detail}
				<div>
					<p class="mb-1 text-xs font-medium text-[var(--color-text-muted)]">{m.conv_detail()}</p>
					<pre
						class="overflow-x-auto rounded-lg p-3 text-xs text-[var(--color-text-muted)]"
						style="background: var(--color-surface-2); border: 1px solid var(--color-border);">{detailEntry.detail}</pre>
				</div>
			{/if}
			{#if detailEntry.classification}
				<div>
					<p class="mb-1 text-xs font-medium text-[var(--color-text-muted)]">
						{m.conv_classification()}
					</p>
					<pre
						class="overflow-x-auto rounded-lg p-3 text-xs text-[var(--color-text-muted)]"
						style="background: var(--color-surface-2); border: 1px solid var(--color-border);">{JSON.stringify(
							detailEntry.classification,
							null,
							2
						)}</pre>
				</div>
			{/if}
			{#if detailEntry.tools_used?.length}
				<div>
					<p class="mb-1 text-xs font-medium text-[var(--color-text-muted)]">
						{m.conv_tools_used()}
					</p>
					<div class="flex flex-wrap gap-1">
						{#each detailEntry.tools_used as tool}
							<span
								class="rounded-md px-2 py-0.5 font-mono text-xs"
								style="background: var(--color-brand-soft); color: var(--color-brand);"
								>{String(tool)}</span
							>
						{/each}
					</div>
				</div>
			{/if}
			{#if detailEntry.flow_steps?.length}
				<div>
					<p class="mb-1 text-xs font-medium text-[var(--color-text-muted)]">
						{m.conv_flow_steps()}
					</p>
					<div class="flex flex-wrap gap-1">
						{#each detailEntry.flow_steps as step}
							<span
								class="rounded-md px-2 py-0.5 font-mono text-xs"
								style="background: var(--color-surface-2); color: var(--color-text-muted);"
								>{String(step)}</span
							>
						{/each}
					</div>
				</div>
			{/if}

			<!-- Send this one turn through the improvement loop -->
			{#if canReview}
				<div
					class="rounded-xl border p-4"
					style="border-color: var(--color-border); background: var(--color-surface-2);"
				>
					<div class="flex flex-wrap items-center gap-3">
						<Button
							size="sm"
							onclick={reviewTurn}
							disabled={$reviewTurnMut.isPending || (reviewRequestId !== null && !reviewSettled)}
						>
							<Microscope size={14} />
							{m.conv_review_turn()}
						</Button>
						{#if reviewRequestId !== null && !reviewSettled}
							<span class="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
								<RefreshCw size={12} class="animate-spin" />
								{$reviewRequest.data?.cancel_requested
									? m.rp_stopping()
									: $reviewRequest.data?.status === 'running'
										? m.conv_review_running()
										: m.conv_review_queued()}
							</span>
							<Button
								variant="secondary"
								size="sm"
								onclick={stopReview}
								disabled={$cancelReviewMut.isPending || $reviewRequest.data?.cancel_requested}
								title={m.rp_stop_hint()}
							>
								<Square size={14} />
								{m.rp_stop()}
							</Button>
						{:else if $reviewRequest.data?.status === 'failed'}
							<span class="text-xs" style="color: var(--color-danger);">
								{$reviewRequest.data.error ?? m.conv_review_failed()}
							</span>
						{:else if $reviewRequest.data?.status === 'cancelled'}
							<span class="text-xs text-[var(--color-text-muted)]">{m.conv_review_cancelled()}</span
							>
						{/if}
						{#if reviewError}
							<span class="text-xs" style="color: var(--color-danger);">{reviewError}</span>
						{/if}
					</div>
					<p class="mt-2 text-xs text-[var(--color-text-muted)]">{m.conv_review_turn_hint()}</p>

					{#if reviewRequestId !== null && $reviewRequest.data?.status === 'requested'}
						<p class="mt-2 text-xs text-[var(--color-text-muted)]">
							{m.conv_review_no_worker_hint()}
						</p>
					{/if}

					{#if reviewSettled && $reviewFinding.data}
						<div class="mt-4 border-t pt-4" style="border-color: var(--color-border);">
							<FindingDetail
								finding={$reviewFinding.data}
								actions
								profileId={detailEntry.profile_id ?? null}
							/>
						</div>
					{:else if reviewSettled && $reviewFinding.isFetched && !$reviewFinding.data}
						<p class="mt-3 text-xs text-[var(--color-text-muted)]">{m.conv_review_no_finding()}</p>
					{/if}
				</div>
			{/if}
		</div>

		<div class="flex justify-end border-t px-6 py-4" style="border-color: var(--color-border);">
			<Button variant="secondary" size="sm" onclick={closeDetail}>{m.common_close()}</Button>
		</div>
	{/if}
</dialog>

<!-- Promote-to-KB dialog -->
<dialog
	bind:this={dialog}
	class="mx-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-2xl overflow-y-auto rounded-2xl p-0 shadow-2xl"
	style="background: var(--color-surface); border: 1px solid var(--color-border);"
>
	<form
		onsubmit={(e) => {
			e.preventDefault();
			submitPromote();
		}}
	>
		<div
			class="flex items-center justify-between border-b px-6 py-4"
			style="border-color: var(--color-border);"
		>
			<h2 class="text-base font-semibold text-[var(--color-text-muted)]">
				{m.conv_promote_title()}
			</h2>
			<button
				type="button"
				onclick={closeDialog}
				class="rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
				aria-label={m.common_close()}><X size={16} /></button
			>
		</div>

		<div class="space-y-4 px-6 py-5">
			<p class="text-xs text-[var(--color-text-muted)]">
				{m.conv_promote_hint()}
			</p>
			<div>
				<label class="field-label" for="fld-page-conversations-1"
					>{m.directives_field_title()}</label
				>
				<input
					id="fld-page-conversations-1"
					type="text"
					bind:value={form.title}
					required
					maxlength="512"
					class="field-input"
				/>
			</div>
			<div>
				<label class="field-label" for="fld-page-conversations-2"
					>{m.directives_field_content()}</label
				>
				<textarea
					id="fld-page-conversations-2"
					bind:value={form.body}
					required
					rows={10}
					class="field-textarea"></textarea>
			</div>
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
				<div>
					<label class="field-label" for="fld-page-conversations-3"
						>{m.directives_field_language()}</label
					>
					<select id="fld-page-conversations-3" bind:value={form.language} class="field-input">
						<option value="bg">bg</option>
						<option value="en">en</option>
					</select>
				</div>
				<div>
					<label class="field-label" for="fld-page-conversations-4"
						>{m.directives_field_legal_year()}</label
					>
					<input
						id="fld-page-conversations-4"
						type="number"
						bind:value={form.legal_year}
						placeholder="default"
						class="field-input"
					/>
				</div>
			</div>
		</div>

		{#if formError}
			<div
				class="mx-6 mb-2 rounded-lg px-3 py-2 text-sm"
				style="background: var(--color-danger-soft); color: var(--color-danger);"
			>
				{formError}
			</div>
		{/if}
		{#if promoteDone}
			<div
				class="mx-6 mb-2 flex items-center gap-2 rounded-lg px-3 py-2 text-sm"
				style="background: var(--color-brand-soft); color: var(--color-brand);"
			>
				<Check size={14} />
				{m.conv_added_kb()}
			</div>
		{/if}

		<div
			class="flex justify-end gap-2 border-t px-6 py-4"
			style="border-color: var(--color-border);"
		>
			<Button type="button" variant="secondary" size="sm" onclick={closeDialog}
				>{promoteDone ? m.common_close() : m.common_cancel()}</Button
			>
			{#if !promoteDone}
				<Button type="submit" size="sm" disabled={$promoteMut.isPending}>
					{$promoteMut.isPending ? m.conv_ingesting() : m.conv_promote()}
				</Button>
			{/if}
		</div>
	</form>
</dialog>
