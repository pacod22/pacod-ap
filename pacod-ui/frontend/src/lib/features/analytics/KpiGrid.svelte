<script lang="ts">
	/** KPI tiles shared by the analytics overview and the per-user page. */
	import { m } from '$lib/paraglide/messages';
	import GatewayConsoleLink from '$lib/components/gateway/GatewayConsoleLink.svelte';
	import type { Metrics } from './queries';
	import { num, usd, compact, pct, fmtDuration, errorRate, avgDwell } from './format';

	let {
		totals,
		errorsByType = {},
		uniqueUsers,
		errorsConsoleUrl,
		loading = false
	}: {
		totals: Metrics | undefined;
		errorsByType?: Record<string, number>;
		/** Omit to hide the tile (the per-user page). */
		uniqueUsers?: number;
		/** The gateway console's Errors page for the same filters, linked from the LLM-errors tile. */
		errorsConsoleUrl?: string | null;
		loading?: boolean;
	} = $props();

	const t = $derived(totals);
	const rate = $derived(t ? errorRate(t) : 0);
	const errorTypes = $derived(
		Object.entries(errorsByType)
			.filter(([, n]) => n > 0)
			.sort((a, b) => b[1] - a[1])
	);

	interface Tile {
		label: string;
		value: string;
		sub?: string;
		danger?: boolean;
		/** Show the error-type breakdown under this tile. */
		errors?: boolean;
	}

	const tiles = $derived<Tile[]>(
		t
			? [
					...(uniqueUsers !== undefined
						? [{ label: m.analytics_kpi_unique_users(), value: num(uniqueUsers) }]
						: []),
					{ label: m.analytics_kpi_page_requests(), value: num(t.page_requests) },
					{ label: m.analytics_kpi_api_requests(), value: num(t.api_requests) },
					{
						label: m.analytics_kpi_turns(),
						value: num(t.turns),
						sub: m.analytics_kpi_turns_sub({ rejections: num(t.rejections) })
					},
					{ label: m.analytics_kpi_llm_calls(), value: num(t.llm_calls) },
					{
						label: m.analytics_kpi_tokens(),
						value: compact(t.input_tokens + t.output_tokens),
						sub: m.analytics_kpi_tokens_sub({
							input: compact(t.input_tokens),
							output: compact(t.output_tokens)
						})
					},
					{ label: m.analytics_kpi_cost(), value: usd(t.cost_usd) },
					{
						label: m.analytics_kpi_llm_errors(),
						value: num(t.llm_errors),
						sub: m.analytics_kpi_error_rate({ rate: pct(rate) }),
						danger: t.llm_errors > 0,
						errors: true
					},
					{ label: m.analytics_kpi_page_views(), value: num(t.page_views) },
					{ label: m.analytics_kpi_avg_dwell(), value: fmtDuration(avgDwell(t)) }
				]
			: []
	);
</script>

{#if loading && !t}
	<p class="text-sm text-[var(--color-text-muted)]">{m.common_loading()}</p>
{:else}
	<div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
		{#each tiles as tile (tile.label)}
			<div class="rounded-xl border p-3" style="border-color: var(--color-border);">
				<div class="text-xs text-[var(--color-text-muted)]">{tile.label}</div>
				<div
					class="mt-1 text-2xl font-semibold tabular-nums"
					style={tile.danger ? 'color: var(--color-danger);' : ''}
				>
					{tile.value}
				</div>
				{#if tile.sub}
					<div class="mt-0.5 text-xs text-[var(--color-text-muted)]">{tile.sub}</div>
				{/if}
				{#if tile.errors && errorTypes.length > 0}
					<div class="mt-1 flex flex-wrap gap-1">
						{#each errorTypes as [type, n] (type)}
							<span
								class="rounded px-1.5 py-0.5 text-[10px]"
								style="background: var(--color-surface-3); color: var(--color-text-muted);"
								>{type}: {num(n)}</span
							>
						{/each}
					</div>
				{/if}
				{#if tile.errors}
					<GatewayConsoleLink
						href={errorsConsoleUrl}
						label={m.gateway_console_open_errors()}
						class="mt-1.5"
					/>
				{/if}
			</div>
		{/each}
	</div>
{/if}
