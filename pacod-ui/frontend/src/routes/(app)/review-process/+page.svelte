<script lang="ts">
	import { untrack } from 'svelte';
	import {
		Play,
		Save,
		X,
		FileText,
		RefreshCw,
		RotateCcw,
		AlertTriangle,
		ChevronLeft,
		ChevronRight,
		ListFilter,
		Square
	} from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import TemplateSelect from '$lib/components/llm-templates/TemplateSelect.svelte';
	import { roleLabel } from '$lib/components/llm-templates/labels';
	import { createTemplatesQuery } from '$lib/features/llm-config/templates';
	import FindingDetail from '$lib/components/FindingDetail.svelte';
	import {
		createLoopConfigQuery,
		createFindingsQuery,
		createClustersQuery,
		createFindingsStatsQuery,
		createQueueStatusQuery,
		createGoldenTestsQuery,
		createFixChecksQuery,
		updateLoopConfigMutation,
		updateClusterMutation,
		enqueueRunMutation,
		cancelRunRequestMutation,
		fetchRunDetail,
		type LoopConfigValues,
		type LoopFinding,
		type LoopRunDetail,
		type LoopFixCheck
	} from '$lib/features/loop/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { m } from '$lib/paraglide/messages';

	let { data } = $props();

	// The SSR snapshot is unscoped (default profile), so it only seeds the query
	// for the global selection; a scoped profile must fetch its own config.
	const cfgQuery = $derived(
		createLoopConfigQuery(selectedProfile.id ? undefined : data.initial, 5000, selectedProfile.id)
	);
	const updateMut = $derived(updateLoopConfigMutation(selectedProfile.id));
	const runMut = $derived(enqueueRunMutation(selectedProfile.id));
	const cancelMut = cancelRunRequestMutation();
	async function stopRequest(id: number) {
		runError = '';
		try {
			await $cancelMut.mutateAsync(id);
		} catch (e: unknown) {
			runError = e instanceof Error ? e.message : m.rp_stop_failed();
		}
	}

	// Findings browser. Every filter is applied SERVER-side (a client filter over a
	// capped page hid hundreds of rows) and defaults to the reviewer's work queue:
	// what nobody has given a verdict yet. Dispositions are what make the
	// precision panel below fillable, so reaching them has to be the default view.
	const PAGE = 50;
	let fRoute = $state('all');
	let fFailureMode = $state('all');
	let fAssessment = $state('all');
	let fDisposition = $state('unreviewed');
	let fRunId = $state<number | null>(null);
	let fClusterId = $state<number | null>(null);
	let fOffset = $state(0);
	// A `?cluster=` link (from a finding's cluster badge) opens that drill-down.
	$effect(() => {
		const q = new URLSearchParams(window.location.search).get('cluster');
		const id = q ? Number(q) : NaN;
		if (Number.isFinite(id)) {
			fClusterId = id;
			fDisposition = 'all';
		}
	});
	const findingsQuery = $derived(
		createFindingsQuery(undefined, 8000, selectedProfile.id, {
			route: fRoute,
			failure_mode: fFailureMode,
			assessment: fAssessment,
			disposition: fDisposition,
			run_id: fRunId,
			cluster_id: fClusterId,
			limit: PAGE,
			offset: fOffset
		})
	);
	const findings = $derived($findingsQuery.data ?? []);
	const filtersNarrowed = $derived(
		fRoute !== 'all' ||
			fFailureMode !== 'all' ||
			fAssessment !== 'all' ||
			fDisposition !== 'unreviewed' ||
			fRunId !== null ||
			fClusterId !== null
	);
	function clearFilters() {
		fRoute = 'all';
		fFailureMode = 'all';
		fAssessment = 'all';
		fDisposition = 'unreviewed';
		fRunId = null;
		fClusterId = null;
		fOffset = 0;
	}
	function showClusterFindings(id: number) {
		clearFilters();
		fClusterId = id;
		fDisposition = 'all';
		document.getElementById('findings')?.scrollIntoView({ behavior: 'smooth' });
	}
	function showRunFindings(id: number) {
		clearFilters();
		fRunId = id;
		fDisposition = 'all';
		reportDialog?.close();
		document.getElementById('findings')?.scrollIntoView({ behavior: 'smooth' });
	}

	// Is the worker draining the queue? The first thing to check when "a review
	// produced nothing" — a banner beats discovering it in a stuck request row.
	const queueQuery = $derived(createQueueStatusQuery(selectedProfile.id));
	const queue = $derived($queueQuery.data ?? null);

	// The regression set and whether claimed fixes held.
	const goldenQuery = $derived(createGoldenTestsQuery(selectedProfile.id));
	const fixChecksQuery = $derived(createFixChecksQuery(selectedProfile.id));
	const goldenTests = $derived($goldenQuery.data ?? []);
	const latestCheck = $derived.by(() => {
		const byTest = new Map<number, LoopFixCheck>();
		for (const c of $fixChecksQuery.data ?? []) {
			if (!byTest.has(c.golden_test_id)) byTest.set(c.golden_test_id, c);
		}
		return byTest;
	});

	// Config form seeded from the SSR snapshot, then re-seeded from the scoped
	// query on a profile switch (Save writes to the selected profile, so the form
	// must show that profile's values, not the default profile's).
	// LLM templates (global) — each review stage may pick one instead of its
	// model / max-tokens fields.
	const templatesQuery = createTemplatesQuery();
	const templates = $derived($templatesQuery.data?.templates ?? []);

	let form = $state<LoopConfigValues>({ ...untrack(() => data.initial.effective) });
	let saveError = $state('');
	let saved = $state(false);
	let seededFor = $state<string | null | undefined>(null);
	$effect(() => {
		const pid = selectedProfile.id;
		const eff = $cfgQuery.data?.effective;
		if (seededFor === pid) return;
		if (!eff) {
			if (seededFor !== undefined) seededFor = undefined;
			return;
		}
		form = { ...eff };
		seededFor = pid;
		saved = false;
		saveError = '';
	});

	const runs = $derived($cfgQuery.data?.recent_runs ?? []);
	const requests = $derived($cfgQuery.data?.recent_requests ?? []);
	const availableModels = $derived($cfgQuery.data?.available_models ?? []);
	// Server-resolved: what the configured panel will actually do next run.
	const panel = $derived($cfgQuery.data?.panel ?? null);

	// Clusters are the decision surface above findings: a recurring problem is
	// actionable in a way that forty separate turns are not.
	let clusterStatus = $state<'open' | 'ticketed' | 'resolved' | 'dismissed'>('open');
	const clustersQuery = $derived(createClustersQuery(selectedProfile.id, clusterStatus));
	const clusterMut = updateClusterMutation();
	const clusters = $derived($clustersQuery.data ?? []);

	// Dismissal rate — the evidence that an analysis change actually helped.
	const statsQuery = $derived(createFindingsStatsQuery(selectedProfile.id));
	const stats = $derived($statsQuery.data ?? null);

	async function setClusterStatus(id: number, status: 'ticketed' | 'resolved' | 'dismissed') {
		try {
			await $clusterMut.mutateAsync({ id, status });
		} catch (e) {
			saveError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}
	// Inline reviewer notes + ticket reference on a cluster (the row IS the ticket).
	let clusterEdit = $state<{ id: number; review_notes: string; ticket_source_id: string } | null>(
		null
	);
	async function saveClusterEdit() {
		if (!clusterEdit) return;
		try {
			await $clusterMut.mutateAsync({ ...clusterEdit });
			clusterEdit = null;
		} catch (e) {
			saveError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	// Options for a model dropdown: the live proxy models, plus the currently-set
	// value if it isn't in that list (custom/stale value or proxy unreachable), so
	// a selection is never silently dropped.
	function modelOptions(current: string): string[] {
		const opts = [...availableModels];
		if (current && !opts.includes(current)) opts.unshift(current);
		return opts;
	}

	async function save() {
		saveError = '';
		saved = false;
		try {
			await $updateMut.mutateAsync({ ...form });
			saved = true;
			setTimeout(() => (saved = false), 2500);
		} catch (e: unknown) {
			saveError = e instanceof Error ? e.message : m.profiles_save_failed();
		}
	}

	let dryRun = $state(false);
	let forceReanalyse = $state(false);
	let includeDismissed = $state(false);
	let runError = $state('');
	async function enqueue(body: {
		dry_run: boolean;
		force?: boolean;
		run_date?: string | null;
		include_dismissed?: boolean;
	}) {
		runError = '';
		try {
			await $runMut.mutateAsync(body);
		} catch (e: unknown) {
			runError = e instanceof Error ? e.message : m.rp_enqueue_failed();
		}
	}
	const runNow = () =>
		enqueue({
			dry_run: dryRun,
			force: forceReanalyse,
			run_date: null,
			include_dismissed: includeDismissed
		});
	// Re-run an existing run/request over the same window. Forced, so already-analysed
	// turns are re-evaluated (otherwise idempotency would skip them and nothing changes).
	const rerun = (src: { run_date: string | null; dry_run: boolean }) =>
		enqueue({ dry_run: src.dry_run, force: true, run_date: src.run_date });

	// Report viewer. `reportError` keeps a failed fetch distinct from "loading".
	let reportDialog = $state<HTMLDialogElement | null>(null);
	let report = $state<LoopRunDetail | null>(null);
	let reportError = $state('');
	async function openReport(id: number) {
		report = null;
		reportError = '';
		reportDialog?.showModal();
		try {
			report = await fetchRunDetail(id);
		} catch (e) {
			reportError = e instanceof Error ? e.message : m.rp_report_load_failed();
		}
	}
	// The stats keys worth a row in the run-detail modal, in reading order.
	const STAT_KEYS = [
		'collected',
		'skipped_idempotent',
		'skipped_dismissed',
		'triaged_in',
		'triaged_out',
		'errors',
		'errors_truncated',
		'errors_unparseable',
		'errors_upstream',
		'errors_timeout',
		'llm_retries',
		'candidates',
		'golden_tests',
		'auto_inserted',
		'analysis_mode',
		'analysis_degraded_reason',
		'judge_independent',
		'single_turn'
	];

	// Finding detail viewer. Tracks the finding by ID and re-reads it from the
	// live query, so a disposition set inside the modal shows up immediately
	// instead of after close-and-reopen (the list refetches every 8 s and would
	// otherwise leave the modal holding a stale object).
	let findingDialog = $state<HTMLDialogElement | null>(null);
	let activeId = $state<number | null>(null);
	let activeSnapshot = $state<LoopFinding | null>(null);
	const activeFinding = $derived(
		activeId === null ? null : (findings.find((f) => f.id === activeId) ?? activeSnapshot)
	);
	function openFinding(f: LoopFinding) {
		activeId = f.id;
		activeSnapshot = f;
		findingDialog?.showModal();
	}

	function assessStyle(a: string | null): string {
		if (a === 'incorrect')
			return 'background: var(--color-danger-soft); color: var(--color-danger);';
		if (a === 'partial') return 'background: var(--color-surface-3); color: var(--color-text);';
		if (a === 'correct') return 'background: var(--color-brand-soft); color: var(--color-brand);';
		return 'background: var(--color-surface-3); color: var(--color-text-muted);';
	}

	function statusStyle(s: string): string {
		if (s === 'done') return 'background: var(--color-brand-soft); color: var(--color-brand);';
		if (s === 'failed') return 'background: var(--color-danger-soft); color: var(--color-danger);';
		if (s === 'running' || s === 'requested')
			return 'background: var(--color-surface-3); color: var(--color-text);';
		// cancelled and anything else: muted.
		return 'background: var(--color-surface-3); color: var(--color-text-muted);';
	}

	function n(stats: Record<string, unknown>, key: string): string {
		const v = stats?.[key];
		return v === undefined || v === null ? '—' : String(v);
	}
</script>

<svelte:head><title>{m.rp_title()}</title></svelte:head>

<div class="space-y-6">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">{m.nav_review_process()}</h1>
		<p class="mt-1 max-w-3xl text-sm text-[var(--color-text-muted)]">
			{m.rp_intro_pre()}<a class="underline" href="/kb-candidates">{m.nav_review()}</a
			>{m.rp_intro_post()}
		</p>
	</div>

	<!-- Worker liveness. If nothing drains the queue every run sits `requested`
	     forever, and a request a dead worker left `running` is reaped on restart. -->
	{#if queue?.stale}
		<p
			class="rounded-lg px-3 py-2 text-sm"
			style="background: var(--color-danger-soft); color: var(--color-danger);"
		>
			<AlertTriangle size={13} class="mb-px inline" />
			{m.rp_queue_stale()}
		</p>
	{:else if queue?.waiting_too_long}
		<p
			class="rounded-lg px-3 py-2 text-sm"
			style="background: var(--color-danger-soft); color: var(--color-danger);"
		>
			<AlertTriangle size={13} class="mb-px inline" />
			{m.rp_queue_waiting({
				count: queue.requested,
				minutes: Math.round((queue.oldest_requested_age_seconds ?? 0) / 60)
			})}
		</p>
	{/if}

	<!-- Run now -->
	<div class="rounded-xl border p-4" style="border-color: var(--color-border);">
		<div class="flex flex-wrap items-center gap-3">
			<Button size="sm" onclick={runNow} disabled={$runMut.isPending}>
				<Play size={14} />
				{m.rp_run_now()}
			</Button>
			<label class="flex items-center gap-2 text-sm">
				<input type="checkbox" bind:checked={dryRun} />
				{m.rp_dry_run()}
			</label>
			<label class="flex items-center gap-2 text-sm">
				<input type="checkbox" bind:checked={forceReanalyse} />
				{m.rp_force_reanalyse()}
			</label>
			<label class="flex items-center gap-2 text-sm" title={m.rp_include_dismissed_hint()}>
				<input type="checkbox" bind:checked={includeDismissed} />
				{m.rp_include_dismissed()}
			</label>
			{#if $runMut.isSuccess}
				<span class="text-xs text-[var(--color-text-muted)]">{m.rp_queued()}</span>
			{/if}
			{#if runError}
				<span class="text-xs" style="color: var(--color-danger);">{runError}</span>
			{/if}
		</div>
	</div>

	<!-- Config form -->
	<form
		onsubmit={(e) => {
			e.preventDefault();
			save();
		}}
		class="space-y-5 rounded-xl border p-4"
		style="border-color: var(--color-border);"
	>
		<h2 class="text-base font-semibold">{m.common_configuration()}</h2>

		<fieldset class="space-y-3">
			<legend class="field-label">{m.rp_schedule()}</legend>
			<label class="flex items-center gap-2 text-sm">
				<input type="checkbox" bind:checked={form.schedule_enabled} />
				{m.rp_enable_schedule()}
			</label>
			<div class="grid grid-cols-2 gap-3 sm:max-w-xs">
				<div>
					<label class="field-label" for="fld-page-review-process-1">{m.rp_hour()}</label><input
						id="fld-page-review-process-1"
						type="number"
						min="0"
						max="23"
						bind:value={form.schedule_hour}
						class="field-input"
					/>
				</div>
				<div>
					<label class="field-label" for="fld-page-review-process-2">{m.rp_minute()}</label><input
						id="fld-page-review-process-2"
						type="number"
						min="0"
						max="59"
						bind:value={form.schedule_minute}
						class="field-input"
					/>
				</div>
			</div>
		</fieldset>

		<fieldset class="grid grid-cols-1 gap-3 sm:grid-cols-3">
			<legend class="field-label">{m.rp_window_concurrency()}</legend>
			<div>
				<label class="field-label" for="fld-page-review-process-3">{m.rp_lookback_hours()}</label
				><input
					id="fld-page-review-process-3"
					type="number"
					min="1"
					bind:value={form.lookback_hours}
					class="field-input"
				/>
			</div>
			<div>
				<label class="field-label" for="fld-page-review-process-4">{m.rp_max_conversations()}</label
				><input
					id="fld-page-review-process-4"
					type="number"
					min="0"
					bind:value={form.max_conversations}
					class="field-input"
				/>
			</div>
			<div>
				<label class="field-label" for="fld-page-review-process-5">{m.rp_concurrency()}</label
				><input
					id="fld-page-review-process-5"
					type="number"
					min="1"
					max="32"
					bind:value={form.concurrency}
					class="field-input"
				/>
			</div>
		</fieldset>

		<fieldset class="space-y-2">
			<legend class="field-label">{m.rp_triage()}</legend>
			<div class="sm:max-w-md">
				<select bind:value={form.triage_mode} class="field-input">
					<option value="model">{m.rp_triage_model_opt()}</option>
					<option value="all">{m.rp_triage_all_opt()}</option>
					<option value="feedback_only">{m.rp_triage_feedback_opt()}</option>
				</select>
			</div>
			<p class="text-xs text-[var(--color-text-muted)]">
				{m.rp_triage_hint()}
			</p>
		</fieldset>

		<fieldset class="space-y-2">
			<legend class="field-label">{m.rp_analysis_mode()}</legend>
			<select bind:value={form.analysis_mode} class="field-input sm:max-w-xs">
				<option value="dual">{m.rp_analysis_mode_dual()}</option>
				<option value="single">{m.rp_analysis_mode_single()}</option>
			</select>
			<p class="text-xs text-[var(--color-text-muted)]">{m.rp_analysis_mode_hint()}</p>
			<!-- What the panel will ACTUALLY do, resolved server-side. A `dual` setting
			     with no distinct model B runs single — saying so here beats discovering
			     it in a run report after the fact. -->
			{#if panel?.degraded_reason}
				<p
					class="rounded-lg px-3 py-2 text-xs"
					style="background: var(--color-danger-soft); color: var(--color-danger);"
				>
					<AlertTriangle size={12} class="mb-px inline" />
					{m.rp_panel_degraded({ reason: panel.degraded_reason })}
				</p>
			{:else if panel && !panel.judge_independent}
				<p
					class="rounded-lg px-3 py-2 text-xs"
					style="background: var(--color-surface-3); color: var(--color-text-muted);"
				>
					{m.rp_judge_not_independent()}
				</p>
			{/if}
		</fieldset>

		<fieldset class="grid grid-cols-1 gap-3 sm:grid-cols-2">
			<legend class="field-label">{m.rp_models_legend()}</legend>
			<p class="text-xs text-[var(--color-text-muted)] sm:col-span-2">
				{m.llmtpl_stage_template_hint()}
			</p>
			<div>
				<TemplateSelect
					id="rp-tpl-triage"
					label={`${roleLabel('triage')} — ${m.llmtpl_stage_template()}`}
					bind:value={form.triage_template}
					{templates}
					params={$cfgQuery.data?.stage_params['triage'] ?? null}
				/>
				{#if !form.triage_template}
					<label class="field-label" for="fld-page-review-process-6">{m.rp_triage_label()}</label>
					<select id="fld-page-review-process-6" bind:value={form.triage_model} class="field-input">
						<option value="">{m.rp_classifier_default()}</option>
						{#each modelOptions(form.triage_model) as mdl (mdl)}<option value={mdl}>{mdl}</option
							>{/each}
					</select>
					<label class="field-label mt-2" for="fld-page-review-process-7">{m.rp_max_tokens()}</label
					>
					<input
						id="fld-page-review-process-7"
						type="number"
						min="1"
						max="32000"
						bind:value={form.triage_max_tokens}
						class="field-input"
					/>
				{/if}
			</div>
			<div>
				<TemplateSelect
					id="rp-tpl-judge"
					label={`${roleLabel('judge')} — ${m.llmtpl_stage_template()}`}
					bind:value={form.judge_template}
					{templates}
					params={$cfgQuery.data?.stage_params['judge'] ?? null}
				/>
				{#if !form.judge_template}
					<label class="field-label" for="fld-page-review-process-8">{m.rp_judge_label()}</label>
					<select id="fld-page-review-process-8" bind:value={form.judge_model} class="field-input">
						<option value="">{m.rp_primary_default()}</option>
						{#each modelOptions(form.judge_model) as mdl (mdl)}<option value={mdl}>{mdl}</option
							>{/each}
					</select>
					<label class="field-label mt-2" for="fld-page-review-process-9">{m.rp_max_tokens()}</label
					>
					<input
						id="fld-page-review-process-9"
						type="number"
						min="1"
						max="32000"
						bind:value={form.judge_max_tokens}
						class="field-input"
					/>
				{/if}
			</div>
			<div>
				<TemplateSelect
					id="rp-tpl-analysis_a"
					label={`${roleLabel('analysis_a')} — ${m.llmtpl_stage_template()}`}
					bind:value={form.analysis_a_template}
					{templates}
					params={$cfgQuery.data?.stage_params['analysis_a'] ?? null}
				/>
				{#if !form.analysis_a_template}
					<label class="field-label" for="fld-page-review-process-10"
						>{m.rp_analysis_a_label()}</label
					>
					<select
						id="fld-page-review-process-10"
						bind:value={form.analysis_model_a}
						class="field-input"
					>
						<option value="">{m.rp_primary_default()}</option>
						{#each modelOptions(form.analysis_model_a) as mdl (mdl)}<option value={mdl}
								>{mdl}</option
							>{/each}
					</select>
					<label class="field-label mt-2" for="fld-page-review-process-11"
						>{m.rp_max_tokens()}</label
					>
					<input
						id="fld-page-review-process-11"
						type="number"
						min="1"
						max="32000"
						bind:value={form.analysis_a_max_tokens}
						class="field-input"
					/>
				{/if}
			</div>
			<!-- Always shown. In dual mode B is the second analyst; in single mode a
			     distinct B is the CRITIC (a model re-checking its own verdict is not a
			     check). Hiding it in single mode left B unconfigurable there. -->
			<div>
				<TemplateSelect
					id="rp-tpl-analysis_b"
					label={`${roleLabel('analysis_b')} — ${m.llmtpl_stage_template()}`}
					bind:value={form.analysis_b_template}
					{templates}
					params={$cfgQuery.data?.stage_params['analysis_b'] ?? null}
				/>
				{#if !form.analysis_b_template}
					<label class="field-label" for="fld-page-review-process-12"
						>{form.analysis_mode === 'dual'
							? m.rp_analysis_b_label()
							: m.rp_analysis_b_single_label()}</label
					>
					<select
						id="fld-page-review-process-12"
						bind:value={form.analysis_model_b}
						class="field-input"
					>
						<option value=""
							>{form.analysis_mode === 'dual'
								? m.rp_analysis_b_unset()
								: m.rp_analysis_b_unset_single()}</option
						>
						{#each modelOptions(form.analysis_model_b) as mdl (mdl)}<option value={mdl}
								>{mdl}</option
							>{/each}
					</select>
					<p class="mt-1 text-xs text-[var(--color-text-muted)]">
						{m.rp_analysis_b_hint({ env: $cfgQuery.data?.env_default.analysis_model_b || '—' })}
					</p>
					{#if form.analysis_mode === 'dual'}
						<label class="field-label mt-2" for="fld-page-review-process-13"
							>{m.rp_max_tokens()}</label
						>
						<input
							id="fld-page-review-process-13"
							type="number"
							min="1"
							max="32000"
							bind:value={form.analysis_b_max_tokens}
							class="field-input"
						/>
					{/if}
				{/if}
			</div>
			<div>
				<TemplateSelect
					id="rp-tpl-remediation"
					label={`${roleLabel('remediation')} — ${m.llmtpl_stage_template()}`}
					bind:value={form.remediation_template}
					{templates}
					params={$cfgQuery.data?.stage_params['remediation'] ?? null}
				/>
				{#if !form.remediation_template}
					<label class="field-label" for="fld-page-review-process-14"
						>{m.rp_remediation_label()}</label
					>
					<select
						id="fld-page-review-process-14"
						bind:value={form.remediation_model}
						class="field-input"
					>
						<option value="">{m.rp_analysis_a_default()}</option>
						{#each modelOptions(form.remediation_model) as mdl (mdl)}<option value={mdl}
								>{mdl}</option
							>{/each}
					</select>
					<label class="field-label mt-2" for="fld-page-review-process-15"
						>{m.rp_max_tokens()}</label
					>
					<input
						id="fld-page-review-process-15"
						type="number"
						min="1"
						max="32000"
						bind:value={form.remediation_max_tokens}
						class="field-input"
					/>
				{/if}
			</div>
		</fieldset>
		{#if availableModels.length === 0}
			<p class="text-xs text-[var(--color-text-muted)]">{m.rp_no_models()}</p>
		{/if}
		<div class="sm:max-w-xs">
			<label class="field-label" for="fld-page-review-process-22">{m.rp_thinking_budget()}</label
			><input
				id="fld-page-review-process-22"
				type="number"
				min="0"
				max="32000"
				bind:value={form.thinking_budget_tokens}
				class="field-input"
			/>
			<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.rp_thinking_budget_hint()}</p>
		</div>

		<fieldset class="grid grid-cols-1 gap-3 sm:grid-cols-3">
			<legend class="field-label">{m.rp_thresholds()}</legend>
			<div>
				<label class="field-label" for="fld-page-review-process-16">{m.rp_novelty_covered()}</label
				><input
					id="fld-page-review-process-16"
					type="number"
					step="0.01"
					min="0"
					max="1"
					bind:value={form.novelty_covered_threshold}
					class="field-input"
				/>
			</div>
			<div>
				<label class="field-label" for="fld-page-review-process-17">{m.rp_novelty_gap()}</label
				><input
					id="fld-page-review-process-17"
					type="number"
					step="0.01"
					min="0"
					max="1"
					bind:value={form.novelty_gap_threshold}
					class="field-input"
				/>
			</div>
			<div>
				<label class="field-label" for="fld-page-review-process-18">{m.rp_grounding()}</label><input
					id="fld-page-review-process-18"
					type="number"
					step="0.01"
					min="0"
					max="1"
					bind:value={form.grounding_threshold}
					class="field-input"
				/>
			</div>
		</fieldset>

		<fieldset class="space-y-3">
			<legend class="field-label">{m.rp_quality_gates()}</legend>
			<label class="flex items-center gap-2 text-sm">
				<input type="checkbox" bind:checked={form.entailment_required} />
				{m.rp_entailment_required()}
			</label>
			<p class="text-xs text-[var(--color-text-muted)]">{m.rp_entailment_hint()}</p>
			<label class="flex items-center gap-2 text-sm">
				<input type="checkbox" bind:checked={form.cluster_enabled} />
				{m.rp_cluster_enabled()}
			</label>
			<div class="sm:max-w-xs">
				<label class="field-label" for="fld-page-review-process-19"
					>{m.rp_cluster_threshold()}</label
				><input
					id="fld-page-review-process-19"
					type="number"
					step="0.01"
					min="0"
					max="1"
					bind:value={form.cluster_threshold}
					class="field-input"
				/>
			</div>
			<label class="flex items-center gap-2 text-sm">
				<input type="checkbox" bind:checked={form.verification_enabled} />
				{m.rp_verification_enabled()}
			</label>
			<p class="text-xs text-[var(--color-text-muted)]">{m.rp_verification_hint()}</p>
			<div class="sm:max-w-xs">
				<label class="field-label" for="fld-page-review-process-20">{m.rp_verification_max()}</label
				><input
					id="fld-page-review-process-20"
					type="number"
					min="1"
					max="500"
					bind:value={form.verification_max_cases}
					class="field-input"
				/>
			</div>
		</fieldset>

		<fieldset class="space-y-3">
			<legend class="field-label">{m.rp_automation()}</legend>
			<label class="flex items-center gap-2 text-sm">
				<input type="checkbox" bind:checked={form.auto_insert_enabled} />
				{m.rp_auto_insert()}
			</label>
			<div class="sm:max-w-xs">
				<label class="field-label" for="fld-page-review-process-21">{m.rp_auto_insert_min()}</label
				><input
					id="fld-page-review-process-21"
					type="number"
					step="0.01"
					min="0"
					max="1"
					bind:value={form.auto_insert_min_confidence}
					class="field-input"
				/>
			</div>
			<label class="flex items-center gap-2 text-sm">
				<input type="checkbox" bind:checked={form.curate_web_search_gaps} />
				{m.rp_curate_web()}
			</label>
			<label class="flex items-center gap-2 text-sm">
				<input type="checkbox" bind:checked={form.meta_retire_enabled} />
				{m.rp_meta_retire()}
			</label>
		</fieldset>

		<div class="flex items-center gap-3">
			<Button type="submit" size="sm" disabled={$updateMut.isPending}
				><Save size={14} /> {m.common_save_configuration()}</Button
			>
			{#if saved}<span class="text-xs" style="color: var(--color-brand);">{m.rp_saved()}</span>{/if}
			{#if saveError}<span class="text-xs" style="color: var(--color-danger);">{saveError}</span
				>{/if}
		</div>
	</form>

	<!-- Recent requests -->
	<div class="space-y-2">
		<div class="flex items-center gap-2">
			<h2 class="text-base font-semibold">{m.rp_recent_requests()}</h2>
			{#if $cfgQuery.isFetching}<RefreshCw
					size={13}
					class="animate-spin text-[var(--color-text-muted)]"
				/>{/if}
		</div>
		<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
			<div class="overflow-x-auto">
				<table class="data-table data-table-stack">
					<thead
						><tr
							><th>#</th><th>{m.rp_col_trigger()}</th><th>{m.common_status()}</th><th
								>{m.rp_col_dry()}</th
							><th>{m.rp_col_by()}</th><th>{m.rp_col_requested()}</th><th>{m.rp_col_run()}</th><th
							></th></tr
						></thead
					>
					<tbody>
						{#if requests.length > 0}
							{#each requests as r (r.id)}
								<tr>
									<td data-label="#" class="tabular-nums">{r.id}</td>
									<td data-label={m.rp_col_trigger()} class="text-xs"
										>{r.trigger}{r.force ? m.rp_force_suffix() : ''}{r.include_dismissed
											? m.rp_include_dismissed_suffix()
											: ''}{#if r.turn_id}<span
												class="ml-1 text-[var(--color-text-muted)]"
												title={r.turn_id}>· {m.rp_single_turn()}</span
											>{/if}</td
									>
									<td data-label={m.common_status()}
										><span
											class="rounded-md px-1.5 py-0.5 text-xs font-medium"
											style={statusStyle(r.status)}>{r.status}</span
										>{#if r.error}<div
												class="max-w-[260px] truncate text-xs"
												style="color: var(--color-danger);"
												title={r.error}
											>
												{r.error}
											</div>{/if}</td
									>
									<td data-label={m.rp_col_dry()} class="text-xs text-[var(--color-text-muted)]"
										>{r.dry_run ? m.rp_yes() : '—'}</td
									>
									<td data-label={m.rp_col_by()} class="text-xs text-[var(--color-text-muted)]"
										>{r.requested_by ?? '—'}</td
									>
									<td
										data-label={m.rp_col_requested()}
										class="text-xs text-[var(--color-text-muted)]"
										>{new Date(r.requested_at).toLocaleString()}</td
									>
									<td data-label={m.rp_col_run()}
										>{#if r.run_id}<button
												class="text-xs underline"
												onclick={() => openReport(r.run_id!)}>#{r.run_id}</button
											>{:else}—{/if}</td
									>
									<td data-label="">
										<div class="flex items-center gap-1">
											{#if r.status === 'requested' || r.status === 'running'}
												<!-- Stop: a queued request is cancelled outright; a running one is
												     flagged and the worker stops at its next safe point. -->
												<button
													class="touch-target rounded-lg p-1.5 hover:text-[var(--color-text)] disabled:opacity-40"
													style="color: var(--color-danger);"
													onclick={() => stopRequest(r.id)}
													disabled={$cancelMut.isPending || r.cancel_requested}
													aria-label={m.rp_stop()}
													title={r.cancel_requested ? m.rp_stopping() : m.rp_stop_hint()}
													><Square size={14} /></button
												>
												{#if r.cancel_requested}<span class="text-xs text-[var(--color-text-muted)]"
														>{m.rp_stopping()}</span
													>{/if}
											{/if}
											<button
												class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-40"
												onclick={() => rerun(r)}
												disabled={$runMut.isPending}
												aria-label={m.rp_rerun()}
												title={m.rp_rerun_request()}><RotateCcw size={14} /></button
											>
										</div>
									</td>
								</tr>
							{/each}
						{:else}
							<tr
								><td colspan="8" class="py-8 text-center text-sm text-[var(--color-text-muted)]"
									>{m.rp_no_requests()}</td
								></tr
							>
						{/if}
					</tbody>
				</table>
			</div>
		</div>
	</div>

	<!-- Recent runs -->
	<div class="space-y-2">
		<h2 class="text-base font-semibold">{m.rp_recent_runs()}</h2>
		<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
			<div class="overflow-x-auto">
				<table class="data-table data-table-stack">
					<thead
						><tr
							><th>#</th><th>{m.rp_col_date()}</th><th>{m.common_status()}</th><th
								>{m.rp_col_collected()}</th
							><th>{m.rp_col_analysed()}</th><th>{m.rp_col_errors()}</th><th
								>{m.rp_col_candidates()}</th
							><th>{m.rp_col_autoins()}</th><th>{m.rp_col_golden()}</th><th></th></tr
						></thead
					>
					<tbody>
						{#if runs.length > 0}
							{#each runs as run (run.id)}
								<tr>
									<td data-label="#" class="tabular-nums">{run.id}</td>
									<td data-label={m.rp_col_date()} class="text-xs"
										>{run.run_date}{run.dry_run ? m.rp_dry_suffix() : ''}{run.stats?.single_turn
											? ` · ${m.rp_single_turn()}`
											: ''}</td
									>
									<td data-label={m.common_status()}
										><span
											class="rounded-md px-1.5 py-0.5 text-xs font-medium"
											style={statusStyle(run.status)}>{run.status}</span
										>{#if run.stats?.analysis_degraded_reason}<span
												class="ml-1 text-xs text-[var(--color-text-muted)]"
												title={String(run.stats.analysis_degraded_reason)}
												>· {String(run.stats.analysis_mode ?? '')}</span
											>{/if}{#if run.error}<div
												class="max-w-[260px] truncate text-xs"
												style="color: var(--color-danger);"
												title={run.error}
											>
												{run.error}
											</div>{/if}</td
									>
									<td
										data-label={m.rp_col_collected()}
										class="text-[var(--color-text-muted)] tabular-nums"
										>{n(run.stats, 'collected')}</td
									>
									<td
										data-label={m.rp_col_analysed()}
										class="text-[var(--color-text-muted)] tabular-nums"
										>{n(run.stats, 'triaged_in')}</td
									>
									<td
										data-label={m.rp_col_errors()}
										class="tabular-nums"
										style={Number(run.stats?.errors ?? 0) > 0
											? 'color: var(--color-danger);'
											: 'color: var(--color-text-muted);'}>{n(run.stats, 'errors')}</td
									>
									<td
										data-label={m.rp_col_candidates()}
										class="text-[var(--color-text-muted)] tabular-nums"
										>{n(run.stats, 'candidates')}</td
									>
									<td
										data-label={m.rp_col_autoins()}
										class="text-[var(--color-text-muted)] tabular-nums"
										>{n(run.stats, 'auto_inserted')}</td
									>
									<td
										data-label={m.rp_col_golden()}
										class="text-[var(--color-text-muted)] tabular-nums"
										>{n(run.stats, 'golden_tests')}</td
									>
									<td data-label="">
										<div class="flex items-center gap-1">
											<button
												class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
												onclick={() => openReport(run.id)}
												aria-label={m.rp_view_report()}
												title={m.rp_view_report()}><FileText size={14} /></button
											>
											<button
												class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-40"
												onclick={() => rerun(run)}
												disabled={$runMut.isPending}
												aria-label={m.rp_rerun()}
												title={m.rp_rerun_date()}><RotateCcw size={14} /></button
											>
										</div>
									</td>
								</tr>
							{/each}
						{:else}
							<tr
								><td colspan="10" class="py-8 text-center text-sm text-[var(--color-text-muted)]"
									>{m.rp_no_runs()}</td
								></tr
							>
						{/if}
					</tbody>
				</table>
			</div>
		</div>
	</div>

	<!-- Clusters: recurring problems, the decision surface above findings. Forty
	     turns failing on one missing source is ONE decision, not forty. -->
	<section class="card">
		<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
			<h2 class="text-base font-semibold">{m.rp_clusters()}</h2>
			<select bind:value={clusterStatus} class="field-input h-8 w-auto py-0 text-sm">
				<option value="open">{m.rp_cluster_open()}</option>
				<option value="ticketed">{m.rp_cluster_ticketed()}</option>
				<option value="resolved">{m.rp_cluster_resolved()}</option>
				<option value="dismissed">{m.rp_cluster_dismissed()}</option>
			</select>
		</div>
		<p class="mb-3 text-xs text-[var(--color-text-muted)]">{m.rp_clusters_help()}</p>
		{#if clusters.length === 0}
			<p class="text-sm text-[var(--color-text-muted)]">{m.rp_clusters_empty()}</p>
		{:else}
			<div class="space-y-2">
				{#each clusters as c (c.id)}
					<div class="rounded-lg border p-3" style="border-color: var(--color-border);">
						<div class="flex flex-wrap items-start justify-between gap-2">
							<div class="min-w-0 flex-1">
								<p class="text-sm font-medium">{c.title}</p>
								{#if c.summary}<p class="mt-0.5 text-xs text-[var(--color-text-muted)]">
										{c.summary}
									</p>{/if}
								{#if c.recommended_action}
									<p class="mt-1 text-xs">
										<strong>{m.rp_cluster_action()}</strong>
										{c.recommended_action}
									</p>
								{/if}
								{#if clusterEdit?.id === c.id}
									<div class="mt-2 space-y-2">
										<input
											type="text"
											class="field-input text-xs"
											placeholder={m.rp_cluster_ticket_placeholder()}
											bind:value={clusterEdit.ticket_source_id}
											maxlength="255"
										/>
										<textarea
											class="field-textarea text-xs"
											rows={2}
											placeholder={m.rp_cluster_notes_placeholder()}
											bind:value={clusterEdit.review_notes}
											maxlength="4000"></textarea>
										<div class="flex gap-1">
											<Button size="sm" onclick={saveClusterEdit} disabled={$clusterMut.isPending}
												>{m.common_save()}</Button
											>
											<Button variant="secondary" size="sm" onclick={() => (clusterEdit = null)}
												>{m.common_cancel()}</Button
											>
										</div>
									</div>
								{:else}
									{#if c.ticket_source_id}
										<p class="mt-1 font-mono text-xs text-[var(--color-text-muted)]">
											{c.ticket_source_id}
										</p>
									{/if}
									{#if c.review_notes}
										<p class="mt-1 text-xs text-[var(--color-text-muted)]">{c.review_notes}</p>
									{/if}
								{/if}
							</div>
							<div class="flex shrink-0 flex-col items-end gap-1">
								<button
									class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5 text-xs underline"
									onclick={() => showClusterFindings(c.id)}
									title={m.rp_cluster_show_findings()}
								>
									{m.rp_cluster_count({ count: c.finding_count })} · {c.route}
								</button>
								{#if c.status !== 'resolved' && c.status !== 'dismissed'}
									<div class="flex gap-1">
										<Button
											variant="secondary"
											size="sm"
											onclick={() =>
												(clusterEdit = {
													id: c.id,
													review_notes: c.review_notes ?? '',
													ticket_source_id: c.ticket_source_id ?? ''
												})}>{m.rp_cluster_notes()}</Button
										>
										{#if c.status === 'open'}
											<Button
												variant="secondary"
												size="sm"
												onclick={() => setClusterStatus(c.id, 'ticketed')}
												>{m.rp_cluster_claim()}</Button
											>
										{/if}
										<Button
											variant="secondary"
											size="sm"
											onclick={() => setClusterStatus(c.id, 'resolved')}
											>{m.rp_cluster_close()}</Button
										>
										<Button
											variant="secondary"
											size="sm"
											onclick={() => setClusterStatus(c.id, 'dismissed')}
											>{m.rp_cluster_dismiss()}</Button
										>
									</div>
								{/if}
							</div>
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</section>

	<!-- Is the loop itself getting better? A dismissal rate that falls after an
	     analysis change is the evidence the change helped. -->
	{#if stats && stats.reviewed > 0}
		<section class="card">
			<h2 class="mb-1 text-base font-semibold">{m.rp_precision()}</h2>
			<p class="mb-3 text-xs text-[var(--color-text-muted)]">{m.rp_precision_help()}</p>
			<p class="text-sm">
				{m.rp_precision_summary({
					reviewed: stats.reviewed,
					total: stats.total,
					rate: stats.dismissal_rate != null ? (stats.dismissal_rate * 100).toFixed(0) : '—'
				})}
			</p>
			<div class="mt-3 overflow-x-auto">
				<table class="w-full text-sm">
					<thead>
						<tr class="text-left text-xs text-[var(--color-text-muted)]">
							<th class="pb-1">{m.kb_mode()}</th>
							<th class="pb-1">{m.rp_precision_evidence()}</th>
							<th class="pb-1">{m.rp_precision_reviewed()}</th>
							<th class="pb-1">{m.rp_precision_dismissed()}</th>
						</tr>
					</thead>
					<tbody>
						{#each stats.by_bucket.filter((b) => b.reviewed > 0) as b (`${b.failure_mode}-${b.evidence_completeness}`)}
							<tr class="border-t" style="border-color: var(--color-border);">
								<td class="py-1">{b.failure_mode ?? '—'}</td>
								<td class="py-1">{b.evidence_completeness ?? '—'}</td>
								<td class="py-1">{b.reviewed}/{b.total}</td>
								<td class="py-1"
									>{b.dismissal_rate != null ? `${(b.dismissal_rate * 100).toFixed(0)}%` : '—'}</td
								>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{/if}

	<!-- Findings: the reviewer's work queue (server-filtered, paged). -->
	<div class="space-y-2" id="findings">
		<div class="flex flex-wrap items-center gap-2">
			<h2 class="text-base font-semibold">{m.rp_findings()}</h2>
			<ListFilter size={13} class="text-[var(--color-text-muted)]" />
			<select
				bind:value={fDisposition}
				onchange={() => (fOffset = 0)}
				class="field-input h-8 w-auto py-0 text-sm"
				aria-label={m.rp_filter_disposition()}
			>
				<option value="unreviewed">{m.rp_disposition_unreviewed()}</option>
				<option value="all">{m.rp_disposition_any()}</option>
				<option value="useful">{m.finding_disposition_useful()}</option>
				<option value="dismissed">{m.finding_disposition_dismissed()}</option>
				<option value="duplicate">{m.finding_disposition_duplicate()}</option>
				<option value="fixed">{m.finding_disposition_fixed()}</option>
			</select>
			<select
				bind:value={fRoute}
				onchange={() => (fOffset = 0)}
				class="field-input h-8 w-auto py-0 text-sm"
				aria-label={m.kb_col_route()}
			>
				<option value="all">{m.rp_route_all()}</option>
				<option value="prompt_fix">{m.rp_route_prompt_fix()}</option>
				<option value="retrieval">{m.rp_route_retrieval()}</option>
				<option value="content_gap">{m.rp_route_content_gap()}</option>
				<option value="none">{m.rp_route_none()}</option>
				<option value="golden">{m.rp_route_golden()}</option>
			</select>
			<select
				bind:value={fAssessment}
				onchange={() => (fOffset = 0)}
				class="field-input h-8 w-auto py-0 text-sm"
				aria-label={m.kb_col_assessment()}
			>
				<option value="all">{m.rp_any_assessment()}</option>
				<option value="incorrect">incorrect</option>
				<option value="partial">partial</option>
				<option value="correct">correct</option>
				<option value="unverifiable">unverifiable</option>
			</select>
			<select
				bind:value={fFailureMode}
				onchange={() => (fOffset = 0)}
				class="field-input h-8 w-auto py-0 text-sm"
				aria-label={m.rp_col_failure_mode()}
			>
				<option value="all">{m.rp_any_failure_mode()}</option>
				<option value="hallucination">hallucination</option>
				<option value="content_gap">content_gap</option>
				<option value="retrieval">retrieval</option>
				<option value="agent_behavior">agent_behavior</option>
				<option value="tone">tone</option>
				<option value="none">none</option>
			</select>
			{#if fClusterId !== null}
				<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5 text-xs"
					>{m.rp_filter_cluster({ id: fClusterId })}</span
				>
			{/if}
			{#if fRunId !== null}
				<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5 text-xs"
					>{m.rp_filter_run({ id: fRunId })}</span
				>
			{/if}
			{#if filtersNarrowed}
				<button class="text-xs underline" onclick={clearFilters}>{m.rp_clear_filters()}</button>
			{/if}
			{#if $findingsQuery.isFetching}<RefreshCw
					size={13}
					class="animate-spin text-[var(--color-text-muted)]"
				/>{/if}
		</div>
		<p class="max-w-3xl text-sm text-[var(--color-text-muted)]">
			{m.rp_findings_help_1()}<a class="underline" href="/kb-candidates">{m.nav_review()}</a
			>{m.rp_findings_help_2()}<a class="underline" href="/agent-prompts"
				>{m.rp_agent_prompt_link()}</a
			>{m.rp_findings_help_3()}<a class="underline" href="/directives">{m.rp_directive_link()}</a
			>{m.rp_findings_help_4()}
		</p>
		<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
			<div class="overflow-x-auto">
				<table class="data-table data-table-stack">
					<thead
						><tr
							><th>{m.kb_col_assessment()}</th><th>{m.rp_col_failure_mode()}</th><th
								>{m.kb_col_route()}</th
							><th>{m.rp_col_fix()}</th><th>{m.rp_col_evidence()}</th><th
								>{m.rp_col_disposition()}</th
							><th>{m.kb_col_conf()}</th><th>{m.kb_col_question()}</th><th>{m.rp_col_created()}</th
							><th></th></tr
						></thead
					>
					<tbody>
						{#if findings.length > 0}
							{#each findings as f (f.id)}
								<tr>
									<td data-label={m.kb_col_assessment()}
										><span
											class="rounded-md px-1.5 py-0.5 text-xs font-medium"
											style={assessStyle(f.assessment)}>{f.assessment ?? '—'}</span
										></td
									>
									<td data-label={m.rp_col_failure_mode()} class="text-xs"
										>{f.failure_mode ?? '—'}</td
									>
									<td data-label={m.kb_col_route()} class="text-xs text-[var(--color-text-muted)]"
										>{f.route ?? '—'}</td
									>
									<td data-label={m.rp_col_fix()} class="text-xs text-[var(--color-text-muted)]"
										>{f.remediation_kind ?? '—'}</td
									>
									<td data-label={m.rp_col_evidence()} class="text-xs"
										><span
											style={f.evidence_completeness === 'full'
												? ''
												: 'color: var(--color-danger);'}>{f.evidence_completeness}</span
										></td
									>
									<td data-label={m.rp_col_disposition()} class="text-xs">{f.disposition ?? '—'}</td
									>
									<td
										data-label={m.kb_col_conf()}
										class="text-xs text-[var(--color-text-muted)] tabular-nums"
										>{f.synthesis_confidence != null ? f.synthesis_confidence.toFixed(2) : '—'}</td
									>
									<td
										data-label={m.kb_col_question()}
										class="max-w-[280px] truncate text-xs"
										title={f.question ?? ''}>{f.question ?? '—'}</td
									>
									<td
										data-label={m.rp_col_created()}
										class="text-xs whitespace-nowrap text-[var(--color-text-muted)]"
										>{new Date(f.created_at).toLocaleDateString()}</td
									>
									<td data-label=""
										><button
											class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
											onclick={() => openFinding(f)}
											aria-label={m.rp_view_finding()}
											title={m.rp_view_finding()}><FileText size={14} /></button
										></td
									>
								</tr>
							{/each}
						{:else}
							<tr
								><td colspan="10" class="py-8 text-center text-sm text-[var(--color-text-muted)]"
									>{m.rp_no_findings()}</td
								></tr
							>
						{/if}
					</tbody>
				</table>
			</div>
		</div>
		<div class="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
			<button
				class="touch-target rounded-lg p-1.5 hover:text-[var(--color-text)] disabled:opacity-40"
				onclick={() => (fOffset = Math.max(0, fOffset - PAGE))}
				disabled={fOffset === 0}
				aria-label={m.rp_prev()}><ChevronLeft size={14} /></button
			>
			<span>{m.rp_page({ page: Math.floor(fOffset / PAGE) + 1 })}</span>
			<button
				class="touch-target rounded-lg p-1.5 hover:text-[var(--color-text)] disabled:opacity-40"
				onclick={() => (fOffset = fOffset + PAGE)}
				disabled={findings.length < PAGE}
				aria-label={m.rp_next()}><ChevronRight size={14} /></button
			>
		</div>
	</div>

	<!-- Regression set: golden tests + whether the last live re-check passed. -->
	<section class="card">
		<h2 class="mb-1 text-base font-semibold">{m.rp_regression()}</h2>
		<p class="mb-3 text-xs text-[var(--color-text-muted)]">{m.rp_regression_help()}</p>
		{#if goldenTests.length === 0}
			<p class="text-sm text-[var(--color-text-muted)]">{m.rp_regression_empty()}</p>
		{:else}
			<div class="overflow-x-auto">
				<table class="data-table data-table-stack">
					<thead
						><tr
							><th>{m.kb_col_question()}</th><th>{m.rp_col_kind_golden()}</th><th
								>{m.rp_col_facts()}</th
							><th>{m.rp_col_edited_by()}</th><th>{m.rp_col_last_check()}</th></tr
						></thead
					>
					<tbody>
						{#each goldenTests as g (g.id)}
							{@const chk = latestCheck.get(g.id)}
							<tr>
								<td
									data-label={m.kb_col_question()}
									class="max-w-[320px] truncate text-xs"
									title={g.question}>{g.question}</td
								>
								<td data-label={m.rp_col_kind_golden()} class="text-xs">{g.kind}</td>
								<td
									data-label={m.rp_col_facts()}
									class="max-w-[280px] truncate text-xs text-[var(--color-text-muted)]"
									title={[...g.expected_facts, ...g.forbidden_facts.map((f) => `¬${f}`)].join(', ')}
									>{g.expected_facts.length + g.forbidden_facts.length || '—'}</td
								>
								<td data-label={m.rp_col_edited_by()} class="text-xs text-[var(--color-text-muted)]"
									>{g.edited_by ?? '—'}</td
								>
								<td data-label={m.rp_col_last_check()} class="text-xs">
									{#if chk}
										<span
											class="rounded-md px-1.5 py-0.5 font-medium"
											style={chk.passed
												? 'background: var(--color-brand-soft); color: var(--color-brand);'
												: 'background: var(--color-danger-soft); color: var(--color-danger);'}
											title={chk.error ??
												[...chk.missing_facts, ...chk.forbidden_hits.map((f) => `¬${f}`)].join(
													', '
												)}>{chk.passed ? m.rp_check_passed() : m.rp_check_failed()}</span
										>
										<span class="ml-1 text-[var(--color-text-muted)]"
											>{new Date(chk.checked_at).toLocaleDateString()}</span
										>
									{:else}
										<span class="text-[var(--color-text-muted)]">{m.rp_check_none()}</span>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</section>
</div>

<!-- Report dialog -->
<dialog
	bind:this={reportDialog}
	class="mx-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-3xl overflow-y-auto rounded-2xl p-0 shadow-2xl"
	style="background: var(--color-surface); border: 1px solid var(--color-border);"
>
	<div
		class="flex items-center justify-between border-b px-6 py-4"
		style="border-color: var(--color-border);"
	>
		<h2 class="text-base font-semibold">{m.rp_run_report()}{report ? ` — #${report.id}` : ''}</h2>
		<button
			type="button"
			onclick={() => reportDialog?.close()}
			class="rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)]"
			aria-label={m.common_close()}><X size={16} /></button
		>
	</div>
	<div class="space-y-4 px-6 py-5">
		{#if reportError}
			<p class="text-sm" style="color: var(--color-danger);">{reportError}</p>
		{:else if report === null}
			<p class="text-sm text-[var(--color-text-muted)]">{m.common_loading()}</p>
		{:else}
			{#if report.error}
				<p
					class="rounded-lg px-3 py-2 text-sm"
					style="background: var(--color-danger-soft); color: var(--color-danger);"
				>
					{m.rp_run_error()}
					{report.error}
				</p>
			{/if}
			{#if report.stats?.analysis_degraded_reason}
				<p
					class="rounded-lg px-3 py-2 text-xs"
					style="background: var(--color-danger-soft); color: var(--color-danger);"
				>
					<AlertTriangle size={12} class="mb-px inline" />
					{m.rp_panel_degraded({ reason: String(report.stats.analysis_degraded_reason) })}
				</p>
			{/if}
			<div>
				<div class="mb-1 flex items-center justify-between">
					<div class="field-label">{m.rp_run_stats()}</div>
					<button class="text-xs underline" onclick={() => showRunFindings(report!.id)}
						>{m.rp_run_findings_link()}</button
					>
				</div>
				<div class="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
					{#each STAT_KEYS.filter((k) => report!.stats?.[k] !== undefined) as k (k)}
						<div
							class="flex justify-between gap-2 border-b py-0.5"
							style="border-color: var(--color-border);"
						>
							<span class="text-[var(--color-text-muted)]">{k}</span>
							<span class="truncate font-medium" title={String(report.stats[k])}
								>{String(report.stats[k])}</span
							>
						</div>
					{/each}
				</div>
			</div>
			{#if report.report_md}
				<pre
					class="max-h-[60vh] overflow-auto rounded-lg p-3 text-xs"
					style="background: var(--color-surface-2); border: 1px solid var(--color-border); white-space: pre-wrap;">{report.report_md}</pre>
			{:else}
				<p class="text-sm text-[var(--color-text-muted)]">{m.rp_no_report()}</p>
			{/if}
		{/if}
	</div>
</dialog>

<!-- Finding detail dialog -->
<dialog
	bind:this={findingDialog}
	class="mx-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-2xl overflow-y-auto rounded-2xl p-0 shadow-2xl"
	style="background: var(--color-surface); border: 1px solid var(--color-border);"
>
	<div
		class="flex items-center justify-between border-b px-6 py-4"
		style="border-color: var(--color-border);"
	>
		<h2 class="text-base font-semibold">
			{m.rp_finding()}{activeFinding ? ` — ${activeFinding.failure_mode ?? '?'}` : ''}
		</h2>
		<button
			type="button"
			onclick={() => findingDialog?.close()}
			class="rounded-lg p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)]"
			aria-label={m.common_close()}><X size={16} /></button
		>
	</div>
	{#if activeFinding}
		<div class="px-6 py-5">
			<FindingDetail finding={activeFinding} actions profileId={selectedProfile.id} />
		</div>
	{/if}
</dialog>
