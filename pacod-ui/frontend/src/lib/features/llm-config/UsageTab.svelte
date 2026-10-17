<script lang="ts">
	import { browser } from '$app/environment';
	import {
		Chart,
		BarController,
		BarElement,
		CategoryScale,
		LinearScale,
		Tooltip,
		Legend
	} from 'chart.js';
	import {
		createUsageOverviewQuery,
		createUsageSummaryQuery,
		type UsageBucket,
		type UsageBreakdownRow
	} from '$lib/features/llm-config/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { profilesQuery } from '$lib/features/profiles/queries';
	import { m } from '$lib/paraglide/messages';
	import GatewayConsoleLink from '$lib/components/gateway/GatewayConsoleLink.svelte';
	import GatewayErrorNotice from '$lib/components/gateway/GatewayErrorNotice.svelte';

	Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip, Legend);

	const BUCKETS: UsageBucket[] = ['hour', 'day', 'week', 'month'];

	// Stable-ish palette cycled across model series (last entry reserved for "Other").
	const PALETTE = [
		'#6366f1',
		'#22c55e',
		'#f59e0b',
		'#ec4899',
		'#06b6d4',
		'#a855f7',
		'#ef4444',
		'#84cc16',
		'#14b8a6'
	];
	const OTHER_COLOR = '#94a3b8';
	const colorFor = (label: string, i: number) =>
		label === 'Other' ? OTHER_COLOR : PALETTE[i % PALETTE.length];

	let bucket = $state<UsageBucket>('day');
	let metric = $state<'cost' | 'tokens'>('cost');
	let providerFilter = $state('');
	let modelFilter = $state('');
	// In-tab profile filter, used only in global mode (when the topbar scope is
	// "All profiles"). When the topbar scopes to a profile, that wins and this is
	// hidden.
	let profileFilter = $state('');

	// The topbar scope ('All profiles' → null) overrides the in-tab filter.
	const scopedProfileId = $derived(selectedProfile.id);
	const effectiveProfileId = $derived(scopedProfileId ?? (profileFilter || undefined));

	// All profiles → an id→name map for readable labels in the dropdown + table.
	const profilesQ = profilesQuery();
	const profileNames = $derived(new Map(($profilesQ.data ?? []).map((p) => [p.id, p.name])));
	const profileLabel = (id: string) => profileNames.get(id) ?? id;

	const overview = $derived(createUsageOverviewQuery(effectiveProfileId));
	const summary = $derived(
		createUsageSummaryQuery(bucket, {
			provider: providerFilter,
			model: modelFilter,
			profile_id: effectiveProfileId
		})
	);

	// Provider derivation mirrors backend `provider_of` (app/services/llm_client.py)
	// so the model dropdown can be scoped to the selected provider.
	function providerOf(model: string): string {
		if (!model) return 'unknown';
		if (model.includes('/')) return model.split('/')[0];
		const low = model.toLowerCase();
		if (low.startsWith('gemini')) return 'google';
		if (low.startsWith('glm')) return 'glm';
		if (low.startsWith('deepseek')) return 'deepseek';
		return model;
	}

	// Option lists for the dropdowns, captured from an unfiltered response so they
	// stay complete once a filter narrows the data.
	let providerOptions = $state<string[]>([]);
	let modelOptions = $state<string[]>([]);
	$effect(() => {
		const d = $summary.data;
		if (d && !providerFilter && !modelFilter) {
			providerOptions = d.by_provider.map((r) => r.key);
			modelOptions = d.by_model.map((r) => r.key);
		}
	});
	const visibleModels = $derived(
		providerFilter ? modelOptions.filter((m) => providerOf(m) === providerFilter) : modelOptions
	);

	function onProviderChange(p: string) {
		providerFilter = p;
		// Drop a model selection that no longer belongs to the chosen provider.
		if (modelFilter && p && providerOf(modelFilter) !== p) modelFilter = '';
	}
	function clearFilters() {
		providerFilter = '';
		modelFilter = '';
		profileFilter = '';
	}

	// Profile dropdown options (global mode). Prefer the full profiles list; fall
	// back to whatever profiles appear in the usage breakdown.
	const profileOptions = $derived(
		($profilesQ.data ?? []).length > 0
			? ($profilesQ.data ?? []).map((p) => ({ id: p.id, name: p.name }))
			: ($summary.data?.by_profile ?? []).map((r) => ({ id: r.key, name: profileLabel(r.key) }))
	);

	const usd = (n: number) =>
		'$' + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
	const num = (n: number) => n.toLocaleString();

	// Format a bucket_start ISO string for the x-axis given the granularity.
	function axisLabel(iso: string): string {
		const d = new Date(iso);
		if (bucket === 'hour')
			return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit' });
		if (bucket === 'month') return d.toLocaleString(undefined, { month: 'short', year: '2-digit' });
		return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
	}

	// Calendar periods in UTC (week from Monday, month from the 1st), not rolling windows.
	const cards = $derived([
		{ label: m.llm_config_usage_this_hour(), w: $overview.data?.hour },
		{ label: m.llm_config_usage_today(), w: $overview.data?.day },
		{ label: m.llm_config_usage_this_week(), w: $overview.data?.week },
		{ label: m.llm_config_usage_this_month(), w: $overview.data?.month }
	]);

	// The LLM figures come from the gateway's reports; `gateway_error` (per response)
	// says they could not be read and the figures read 0. One console link per view;
	// the chart's is dropped when it is the same URL as the cards'.
	const summaryConsoleUrl = $derived(
		$summary.data?.console_url && $summary.data.console_url !== $overview.data?.console_url
			? $summary.data.console_url
			: null
	);

	// ----- Chart.js bar chart (cost per time bucket) -----
	let canvas = $state<HTMLCanvasElement | null>(null);
	let chart: Chart | null = null;

	// Format a y-axis value / tooltip according to the selected metric.
	const fmtMetric = (v: number) => (metric === 'cost' ? usd(v) : num(v) + ' tok');
	const fmtAxis = (v: number | string) => (metric === 'cost' ? '$' + v : num(Number(v)));

	$effect(() => {
		if (!browser || !canvas) return;
		const points = $summary.data?.timeseries ?? [];
		const series = $summary.data?.model_series ?? [];
		const m = metric; // track the toggle so the effect re-runs on change
		const labels = points.map((p) => axisLabel(p.bucket_start));
		const datasets = series.map((s, i) => ({
			label: s.model,
			data: m === 'cost' ? s.cost_usd.map((c) => Number(c.toFixed(6))) : s.tokens,
			backgroundColor: colorFor(s.model, i),
			borderWidth: 0,
			stack: 'metric'
		}));

		if (!chart) {
			chart = new Chart(canvas, {
				type: 'bar',
				data: { labels, datasets },
				options: {
					responsive: true,
					maintainAspectRatio: false,
					plugins: {
						legend: {
							display: true,
							position: 'bottom',
							labels: { boxWidth: 12, font: { size: 10 } }
						},
						tooltip: {
							callbacks: {
								label: (c) => `${c.dataset.label}: ${fmtMetric(Number(c.raw))}`
							}
						}
					},
					scales: {
						x: {
							stacked: true,
							grid: { display: false },
							ticks: { autoSkip: true, maxRotation: 0 }
						},
						y: { stacked: true, beginAtZero: true, ticks: { callback: (v) => fmtAxis(v) } }
					}
				}
			});
		} else {
			chart.data.labels = labels;
			chart.data.datasets = datasets;
			// y-axis tick + tooltip formatters close over `metric`; refresh them.
			const sc = chart.options.scales?.y;
			if (sc && 'ticks' in sc && sc.ticks) sc.ticks.callback = (v) => fmtAxis(v as number);
			const tt = chart.options.plugins?.tooltip;
			if (tt?.callbacks)
				tt.callbacks.label = (c) => `${c.dataset.label}: ${fmtMetric(Number(c.raw))}`;
			chart.update();
		}
	});

	$effect(() => () => {
		chart?.destroy();
		chart = null;
	});

	// By-profile rows with the profile_id key replaced by a readable name.
	const byProfileRows = $derived(
		($summary.data?.by_profile ?? []).map((r) => ({ ...r, key: profileLabel(r.key) }))
	);

	const tables: { title: string; rows: UsageBreakdownRow[] | undefined }[] = $derived([
		{ title: 'By profile (agent)', rows: byProfileRows },
		{ title: 'By provider', rows: $summary.data?.by_provider },
		{ title: 'By model', rows: $summary.data?.by_model },
		{ title: 'By purpose / role', rows: $summary.data?.by_purpose },
		{ title: 'Top users (by cost)', rows: $summary.data?.by_user }
	]);
</script>

<div class="space-y-8">
	<p class="max-w-2xl text-sm text-[var(--color-text-muted)]">
		Token usage and cost of every LLM call as the gateway recorded it (priced with the gateway's
		rates when the call ran), plus web searches — their cost is an <em>estimate</em> per query, not a
		billed figure.
	</p>

	<!-- Calendar-period cost cards (UTC) -->
	<div class="space-y-2">
		<div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
			<p class="text-xs text-[var(--color-text-muted)]">{m.llm_config_usage_periods_hint()}</p>
			<GatewayConsoleLink href={$overview.data?.console_url} />
		</div>
		<GatewayErrorNotice error={$overview.data?.gateway_error} />
		<div class="grid grid-cols-2 gap-4 md:grid-cols-4">
			{#each cards as c}
				<div class="rounded-xl border p-4" style="border-color: var(--color-border);">
					<div class="field-label">{c.label}</div>
					{#if $overview.isPending}
						<div class="mt-1 text-sm text-[var(--color-text-muted)]">…</div>
					{:else}
						<div class="mt-1 text-2xl font-semibold tracking-tight">{usd(c.w?.cost_usd ?? 0)}</div>
						<div class="mt-1 text-xs text-[var(--color-text-muted)]">
							{num((c.w?.input_tokens ?? 0) + (c.w?.output_tokens ?? 0))} tok · {num(
								c.w?.event_count ?? 0
							)} calls
						</div>
					{/if}
				</div>
			{/each}
		</div>
	</div>

	<!-- Period selector + chart -->
	<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
		<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
			<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
				<h2 class="text-base font-semibold">
					{metric === 'cost' ? 'Cost' : 'Tokens'} over time by model
				</h2>
				<GatewayConsoleLink href={summaryConsoleUrl} />
			</div>
			<div class="flex flex-wrap items-center gap-3">
				<div class="flex gap-1">
					{#each [['cost', 'Cost'], ['tokens', 'Tokens']] as [val, lbl]}
						<button
							type="button"
							onclick={() => (metric = val as 'cost' | 'tokens')}
							class="rounded px-2.5 py-1 text-xs"
							style={metric === val
								? 'background: var(--color-brand); color: white;'
								: 'background: var(--color-surface-3); color: var(--color-text-muted);'}
						>
							{lbl}
						</button>
					{/each}
				</div>
				<div class="flex gap-1">
					{#each BUCKETS as b}
						<button
							type="button"
							onclick={() => (bucket = b)}
							class="rounded px-2.5 py-1 text-xs capitalize"
							style={bucket === b
								? 'background: var(--color-brand); color: white;'
								: 'background: var(--color-surface-3); color: var(--color-text-muted);'}
						>
							{b}
						</button>
					{/each}
				</div>
			</div>
		</div>

		<!-- Filters -->
		<div class="mb-4 flex flex-wrap items-end gap-3">
			{#if scopedProfileId}
				<!-- Topbar has scoped the whole console to one profile; usage follows it. -->
				<div>
					<label class="field-label" for="usage-profile-scoped">Profile (agent)</label>
					<div
						id="usage-profile-scoped"
						class="field-input flex items-center"
						style="opacity: 0.85;"
						title="Scoped by the profile selector in the top bar"
					>
						{profileLabel(scopedProfileId)}
					</div>
				</div>
			{:else}
				<div>
					<label class="field-label" for="usage-profile">Profile (agent)</label>
					<select id="usage-profile" class="field-input" bind:value={profileFilter}>
						<option value="">All profiles</option>
						{#each profileOptions as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
					</select>
				</div>
			{/if}
			<div>
				<label class="field-label" for="usage-provider">Provider</label>
				<select
					id="usage-provider"
					class="field-input"
					value={providerFilter}
					onchange={(e) => onProviderChange((e.currentTarget as HTMLSelectElement).value)}
				>
					<option value="">All providers</option>
					{#each providerOptions as p}<option value={p}>{p}</option>{/each}
				</select>
			</div>
			<div>
				<label class="field-label" for="usage-model">Model</label>
				<select id="usage-model" class="field-input" bind:value={modelFilter}>
					<option value="">All models</option>
					{#each visibleModels as m}<option value={m}>{m}</option>{/each}
				</select>
			</div>
			{#if providerFilter || modelFilter || profileFilter}
				<button
					type="button"
					onclick={clearFilters}
					class="rounded px-2.5 py-1 text-xs"
					style="background: var(--color-surface-3); color: var(--color-text-muted);"
				>
					Clear filters
				</button>
			{/if}
		</div>
		{#if !$summary.isError}
			<!-- The chart and the tables below are this response: its LLM figures read 0. -->
			<GatewayErrorNotice error={$summary.data?.gateway_error} class="mb-4" />
		{/if}
		{#if $summary.isError}
			<p class="text-sm" style="color: var(--color-danger);">Failed to load usage data.</p>
		{:else if $summary.data && $summary.data.timeseries.length === 0}
			<!-- With a gateway error the notice above explains the empty chart instead. -->
			{#if !$summary.data.gateway_error}
				<p class="text-sm text-[var(--color-text-muted)]">No usage recorded in this period.</p>
			{/if}
		{:else}
			<div style="height: 280px;"><canvas bind:this={canvas}></canvas></div>
		{/if}
	</section>

	<!-- Breakdown tables -->
	<div class="grid grid-cols-1 gap-6 lg:grid-cols-1">
		{#each tables as t}
			<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
				<h2 class="mb-3 text-base font-semibold">{t.title}</h2>
				{#if $summary.isPending}
					<p class="text-sm text-[var(--color-text-muted)]">Loading…</p>
				{:else if !t.rows || t.rows.length === 0}
					<p class="text-sm text-[var(--color-text-muted)]">No data.</p>
				{:else}
					<table class="data-table">
						<thead>
							<tr>
								<th>{t.title === 'Top users (by cost)' ? 'User' : 'Name'}</th>
								<th style="text-align: right">In</th>
								<th style="text-align: right">Out</th>
								<th style="text-align: right">Cached</th>
								<th style="text-align: right">Cost</th>
								<th style="text-align: right">Calls</th>
							</tr>
						</thead>
						<tbody>
							{#each t.rows as r (r.key)}
								<tr>
									<td class="font-mono text-xs">{r.key}</td>
									<td class="text-right">{num(r.input_tokens)}</td>
									<td class="text-right">{num(r.output_tokens)}</td>
									<td class="text-right">{num(r.cached_tokens)}</td>
									<td class="text-right">{usd(r.cost_usd)}</td>
									<td class="text-right">{num(r.event_count)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				{/if}
			</section>
		{/each}
	</div>
</div>
