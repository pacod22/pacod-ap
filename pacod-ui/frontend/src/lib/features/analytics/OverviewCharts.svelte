<script lang="ts">
	/** The three over-time charts on the analytics overview. */
	import { m } from '$lib/paraglide/messages';
	import TimeSeriesChart from './TimeSeriesChart.svelte';
	import type { AnalyticsBucket, Overview } from './queries';
	import { fillSeries, bucketLabel, compact, COLORS } from './format';

	let {
		data,
		bucket,
		loading = false
	}: { data: Overview | undefined; bucket: AnalyticsBucket; loading?: boolean } = $props();

	const points = $derived(data ? fillSeries(data.series, data.since, bucket) : []);
	const labels = $derived(points.map((p) => bucketLabel(p.bucket, bucket)));
	const hasData = $derived((data?.series.length ?? 0) > 0);

	const charts = $derived([
		{
			title: m.analytics_chart_requests(),
			format: (v: number) => compact(v),
			series: [
				{
					label: m.analytics_col_page_requests(),
					data: points.map((p) => p.page_requests),
					color: COLORS.page
				},
				{
					label: m.analytics_col_api_requests(),
					data: points.map((p) => p.api_requests),
					color: COLORS.api
				},
				{ label: m.analytics_col_turns(), data: points.map((p) => p.turns), color: COLORS.turns }
			]
		},
		{
			title: m.analytics_chart_llm(),
			format: (v: number) => compact(v),
			series: [
				{
					label: m.analytics_col_llm_calls(),
					data: points.map((p) => p.llm_calls),
					color: COLORS.calls
				},
				{
					label: m.analytics_col_errors(),
					data: points.map((p) => p.llm_errors),
					color: COLORS.errors,
					bar: true
				}
			]
		},
		{
			title: m.analytics_chart_tokens(),
			format: (v: number) => compact(v),
			series: [
				{
					label: m.analytics_col_input_tokens(),
					data: points.map((p) => p.input_tokens),
					color: COLORS.input
				},
				{
					label: m.analytics_col_output_tokens(),
					data: points.map((p) => p.output_tokens),
					color: COLORS.output
				}
			]
		}
	]);
</script>

<div class="grid grid-cols-1 gap-4 xl:grid-cols-3">
	{#each charts as c (c.title)}
		<section class="rounded-xl border p-4" style="border-color: var(--color-border);">
			<h2 class="mb-3 text-sm font-semibold">{c.title}</h2>
			{#if !hasData}
				<p class="py-16 text-center text-sm text-[var(--color-text-muted)]">
					{loading ? m.common_loading() : m.analytics_no_data()}
				</p>
			{:else}
				<TimeSeriesChart {labels} series={c.series} format={c.format} height={220} />
			{/if}
		</section>
	{/each}
</div>
