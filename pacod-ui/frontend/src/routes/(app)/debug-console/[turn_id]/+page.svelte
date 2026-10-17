<script lang="ts">
	import { page } from '$app/state';
	import { ArrowLeft, ChevronRight, ChevronDown, AlertCircle } from 'lucide-svelte';
	import {
		createDebugTurnsQuery,
		createDebugTurnStepsQuery,
		fetchDebugInteraction,
		type DebugInteractionRead
	} from '$lib/features/debug-console/queries';
	import { m } from '$lib/paraglide/messages';

	const turnId = $derived(page.params.turn_id ?? '');

	// Header summary (single row) + the ordered steps for this turn.
	const summaryQuery = $derived(createDebugTurnsQuery({ turn_id: turnId, limit: 1 }));
	const summary = $derived($summaryQuery.data?.[0] ?? null);
	const stepsQuery = $derived(createDebugTurnStepsQuery(turnId));
	const steps = $derived($stepsQuery.data ?? []);

	// Lazy full-payload cache, keyed by interaction id.
	type FullState =
		| { status: 'loading' }
		| { status: 'loaded'; data: DebugInteractionRead }
		| { status: 'error'; message: string };
	let expanded = $state<Record<number, boolean>>({});
	let fullCache = $state<Record<number, FullState>>({});

	async function toggleStep(id: number) {
		expanded[id] = !expanded[id];
		if (expanded[id] && !fullCache[id]) {
			fullCache[id] = { status: 'loading' };
			try {
				const data = await fetchDebugInteraction(id);
				fullCache[id] = { status: 'loaded', data };
			} catch (e: unknown) {
				fullCache[id] = {
					status: 'error',
					message: e instanceof Error ? e.message : m.common_failed_to_load()
				};
			}
		}
	}

	function fmtTime(iso: string) {
		return new Date(iso).toLocaleString();
	}
	function fmtDuration(ms: number | null) {
		if (ms === null) return '—';
		return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
	}
	function prettyJson(v: unknown) {
		try {
			return JSON.stringify(v, null, 2);
		} catch {
			return String(v);
		}
	}
	// ttft_ms is nested in the full response payload (llm steps only).
	function ttft(full: FullState | undefined): number | null {
		if (!full || full.status !== 'loaded') return null;
		const resp = full.data.response;
		if (resp && typeof resp === 'object' && 'ttft_ms' in resp) {
			const v = (resp as Record<string, unknown>).ttft_ms;
			return typeof v === 'number' ? v : null;
		}
		return null;
	}

	// Kind → accent colour for the process nodes (theme-aware tokens).
	const KIND_COLOR: Record<string, string> = {
		llm: 'var(--color-brand)',
		rag: 'var(--color-success)',
		tool: 'var(--color-warning)',
		cache: 'var(--color-gold)',
		decision: 'var(--color-text-faint)'
	};
	function kindColor(kind: string) {
		return KIND_COLOR[kind] ?? 'var(--color-text-faint)';
	}
	const KIND_BADGE: Record<string, string> = {
		llm: 'background: var(--color-brand-soft); color: var(--color-brand);',
		rag: 'background: var(--color-success-soft); color: var(--color-success);',
		tool: 'background: var(--color-warning-soft); color: var(--color-warning);',
		cache: 'background: var(--color-gold-soft, var(--color-surface-3)); color: var(--color-gold);',
		decision: 'background: var(--color-surface-3); color: var(--color-text-muted);'
	};
	function kindBadge(kind: string) {
		return (
			KIND_BADGE[kind] ?? 'background: var(--color-surface-3); color: var(--color-text-muted);'
		);
	}
	const KIND_LEGEND: [string, string][] = [
		['llm', 'LLM'],
		['rag', 'RAG'],
		['tool', 'Tool'],
		['cache', 'Cache'],
		['decision', 'Decision']
	];

	// Longest step in this turn — scales the per-block duration bars.
	const maxDuration = $derived(Math.max(1, ...steps.map((s) => s.duration_ms ?? 0)));
</script>

<svelte:head><title>{m.debug_detail_title({ id: turnId.slice(0, 8) })}</title></svelte:head>

<div class="space-y-6">
	<a
		href="/debug-console"
		class="inline-flex items-center gap-1.5 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
	>
		<ArrowLeft size={15} />
		{m.debug_detail_back()}
	</a>

	<!-- Header -->
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">{m.debug_detail_heading()}</h1>
		<p class="mt-1 font-mono text-xs text-[var(--color-text-muted)]">{turnId}</p>
	</div>

	<div
		class="rounded-xl border p-5"
		style="background: var(--color-surface-2); border-color: var(--color-border);"
	>
		{#if summary}
			<div class="mb-3 text-sm">{summary.user_message ?? m.debug_detail_no_message()}</div>
			{#if summary.error}
				<div
					class="mb-3 flex items-start gap-2 rounded-lg px-3 py-2 text-xs"
					style="background: var(--color-danger-soft); color: var(--color-danger);"
				>
					<AlertCircle size={14} class="mt-0.5 shrink-0" />
					<span>{m.debug_detail_rejected()} <span class="font-mono">{summary.error}</span></span>
				</div>
			{/if}
			<div class="flex flex-wrap gap-x-6 gap-y-1 text-xs text-[var(--color-text-muted)]">
				<span
					>{m.debug_console_col_user()}:
					<span class="font-mono">{summary.user_email ?? summary.user_id ?? '—'}</span></span
				>
				<span
					>{m.debug_console_col_agent()}:
					<span class="font-medium text-[var(--color-text)]"
						>{summary.profile_name ?? summary.profile_id ?? '—'}</span
					></span
				>
				<span
					>{m.debug_detail_session()}:
					<span class="font-mono">{summary.session_id ?? '—'}</span></span
				>
				<span>{m.debug_detail_started()}: {fmtTime(summary.started_at)}</span>
				<span>{m.debug_console_col_steps()}: {summary.interaction_count}</span>
				<span>{m.debug_detail_total()}: {fmtDuration(summary.total_duration_ms)}</span>
				{#if summary.error_count > 0}
					<span style="color: var(--color-danger);"
						>{m.debug_console_col_errors()}: {summary.error_count}</span
					>
				{/if}
			</div>
		{:else if $summaryQuery.isPending}
			<div class="text-sm text-[var(--color-text-muted)]">{m.common_loading()}</div>
		{:else}
			<div class="text-sm text-[var(--color-text-muted)]">{m.debug_detail_no_summary()}</div>
		{/if}
	</div>

	<!-- Process flow — one block per interaction, connected top → bottom -->
	<div class="space-y-3">
		<div class="flex flex-wrap items-center justify-between gap-3">
			<h2 class="text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
				{m.debug_detail_process()}
			</h2>
			<div class="flex flex-wrap items-center gap-3 text-[11px] text-[var(--color-text-muted)]">
				{#each KIND_LEGEND as [k, label]}
					<span class="inline-flex items-center gap-1.5">
						<span class="h-2.5 w-2.5 rounded-full" style="background: {kindColor(k)};"
						></span>{label}
					</span>
				{/each}
			</div>
		</div>

		{#if $stepsQuery.isPending}
			<div class="py-8 text-center text-sm text-[var(--color-text-muted)]">
				{m.debug_detail_loading_steps()}
			</div>
		{:else if $stepsQuery.isError}
			<div class="py-8 text-center text-sm text-[var(--color-danger)]">
				{m.debug_detail_load_steps_failed()}
			</div>
		{:else if steps.length === 0}
			<div class="py-8 text-center text-sm text-[var(--color-text-muted)]">
				{summary?.error ? m.debug_detail_rejected_no_steps() : m.debug_detail_no_interactions()}
			</div>
		{:else}
			<div>
				{#each steps as step, i (step.id)}
					{@const isOpen = !!expanded[step.id]}
					{@const full = fullCache[step.id]}
					<div class="relative flex gap-3 pb-2">
						<!-- rail: vertical connector + node dot -->
						<div class="relative flex w-5 shrink-0 justify-center">
							{#if i < steps.length - 1}
								<span
									class="absolute top-5 -bottom-2 left-1/2 w-px -translate-x-1/2"
									style="background: var(--color-border-strong);"
								></span>
							{/if}
							<span
								class="relative z-10 mt-2 h-3.5 w-3.5 rounded-full ring-4"
								style="background: {step.error
									? 'var(--color-danger)'
									: kindColor(step.kind)}; --tw-ring-color: var(--color-surface);"
							></span>
						</div>

						<!-- block -->
						<div
							class="min-w-0 flex-1 overflow-hidden rounded-xl border"
							style="border-color: {isOpen
								? 'var(--color-border-strong)'
								: 'var(--color-border)'}; background: var(--color-surface-2);"
						>
							<button
								type="button"
								onclick={() => toggleStep(step.id)}
								class="flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-[var(--color-surface-3)]"
							>
								<span
									class="w-5 shrink-0 text-right font-mono text-xs text-[var(--color-text-faint)]"
									>{step.seq ?? '·'}</span
								>
								<span
									class="shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase"
									style={kindBadge(step.kind)}>{step.kind}</span
								>
								<span class="min-w-0 flex-1 truncate font-medium">{step.name}</span>
								{#if ttft(full) !== null}
									<span
										class="hidden shrink-0 font-mono text-[11px] text-[var(--color-text-muted)] sm:inline"
										>ttft {fmtDuration(ttft(full))}</span
									>
								{/if}
								<!-- proportional duration bar -->
								<span
									class="hidden h-1.5 w-20 shrink-0 overflow-hidden rounded-full md:block"
									style="background: var(--color-surface-3);"
								>
									<span
										class="block h-full rounded-full"
										style="width: {Math.max(
											3,
											((step.duration_ms ?? 0) / maxDuration) * 100
										)}%; background: {kindColor(step.kind)};"
									></span>
								</span>
								<span
									class="w-16 shrink-0 text-right font-mono text-xs text-[var(--color-text-muted)] tabular-nums"
									>{fmtDuration(step.duration_ms)}</span
								>
								{#if step.error}
									<span class="shrink-0" style="color: var(--color-danger);"
										><AlertCircle size={13} /></span
									>
								{/if}
								{#if isOpen}<ChevronDown
										size={14}
										class="shrink-0 text-[var(--color-text-muted)]"
									/>{:else}<ChevronRight
										size={14}
										class="shrink-0 text-[var(--color-text-muted)]"
									/>{/if}
							</button>

							{#if isOpen}
								<div class="border-t px-3 py-3" style="border-color: var(--color-border);">
									{#if step.error}
										<div
											class="mb-3 rounded-lg px-3 py-2 text-xs"
											style="background: var(--color-danger-soft); color: var(--color-danger);"
										>
											{step.error}
										</div>
									{/if}
									{#if !full || full.status === 'loading'}
										<div class="text-xs text-[var(--color-text-muted)]">
											{m.debug_detail_loading_payload()}
										</div>
									{:else if full.status === 'error'}
										<div class="text-xs text-[var(--color-danger)]">{full.message}</div>
									{:else}
										<div class="space-y-3">
											<div>
												<div
													class="mb-1 font-mono text-[10px] font-semibold tracking-wide text-[var(--color-text-faint)] uppercase"
												>
													{m.debug_detail_request()}
												</div>
												<pre
													class="max-h-96 overflow-auto rounded-lg p-3 text-xs"
													style="background: var(--color-surface-3);">{prettyJson(
														full.data.request
													)}</pre>
											</div>
											<div>
												<div
													class="mb-1 font-mono text-[10px] font-semibold tracking-wide text-[var(--color-text-faint)] uppercase"
												>
													{m.debug_detail_response()}
												</div>
												<pre
													class="max-h-96 overflow-auto rounded-lg p-3 text-xs"
													style="background: var(--color-surface-3);">{prettyJson(
														full.data.response
													)}</pre>
											</div>
										</div>
									{/if}
								</div>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>
