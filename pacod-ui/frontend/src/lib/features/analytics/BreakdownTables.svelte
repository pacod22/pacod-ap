<script lang="ts">
	/** By-domain, by-domain×agent and by-model tables, shared by both analytics pages. */
	import { m } from '$lib/paraglide/messages';
	import type { Overview } from './queries';
	import { num, usd, compact, pct, fmtDuration, avgDwell, siteLabel } from './format';

	let {
		data,
		loading = false,
		profileLabel
	}: {
		data: Overview | undefined;
		loading?: boolean;
		profileLabel: (id: string) => string;
	} = $props();

	const bySite = $derived(data?.by_site ?? []);
	const bySiteProfile = $derived(data?.by_site_profile ?? []);
	const byModel = $derived(data?.by_model ?? []);
</script>

{#snippet empty()}
	<p class="p-4 text-sm text-[var(--color-text-muted)]">
		{loading ? m.common_loading() : m.analytics_no_data()}
	</p>
{/snippet}

{#snippet siteCell(site: string)}
	<span class="font-medium">{siteLabel(site)}</span>
	{#if siteLabel(site) !== site}
		<span class="ml-1 font-mono text-[10px] text-[var(--color-text-muted)]">{site}</span>
	{/if}
{/snippet}

<section class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
	<h2 class="px-4 pt-3 pb-2 text-base font-semibold">{m.analytics_by_site_title()}</h2>
	{#if bySite.length === 0}
		{@render empty()}
	{:else}
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.analytics_col_site()}</th>
						<th style="text-align: right">{m.analytics_col_page_requests()}</th>
						<th style="text-align: right">{m.analytics_col_api_requests()}</th>
						<th style="text-align: right">{m.analytics_col_turns()}</th>
						<th style="text-align: right">{m.analytics_col_llm_calls()}</th>
						<th style="text-align: right">{m.analytics_col_tokens()}</th>
						<th style="text-align: right">{m.analytics_col_cost()}</th>
						<th style="text-align: right">{m.analytics_col_errors()}</th>
						<th style="text-align: right">{m.analytics_col_page_views()}</th>
						<th style="text-align: right">{m.analytics_col_avg_dwell()}</th>
					</tr>
				</thead>
				<tbody>
					{#each bySite as r (r.site)}
						<tr>
							<td data-label={m.analytics_col_site()}>{@render siteCell(r.site)}</td>
							<td data-label={m.analytics_col_page_requests()} class="text-right tabular-nums"
								>{num(r.page_requests)}</td
							>
							<td data-label={m.analytics_col_api_requests()} class="text-right tabular-nums"
								>{num(r.api_requests)}</td
							>
							<td data-label={m.analytics_col_turns()} class="text-right tabular-nums"
								>{num(r.turns)}</td
							>
							<td data-label={m.analytics_col_llm_calls()} class="text-right tabular-nums"
								>{num(r.llm_calls)}</td
							>
							<td data-label={m.analytics_col_tokens()} class="text-right tabular-nums"
								>{compact(r.input_tokens + r.output_tokens)}</td
							>
							<td data-label={m.analytics_col_cost()} class="text-right tabular-nums"
								>{usd(r.cost_usd)}</td
							>
							<td
								data-label={m.analytics_col_errors()}
								class="text-right tabular-nums"
								style={r.llm_errors > 0 ? 'color: var(--color-danger);' : ''}
								>{num(r.llm_errors)}</td
							>
							<td data-label={m.analytics_col_page_views()} class="text-right tabular-nums"
								>{num(r.page_views)}</td
							>
							<td data-label={m.analytics_col_avg_dwell()} class="text-right tabular-nums"
								>{r.page_views ? fmtDuration(avgDwell(r)) : '—'}</td
							>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</section>

<section class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
	<h2 class="px-4 pt-3 pb-2 text-base font-semibold">{m.analytics_by_site_profile_title()}</h2>
	{#if bySiteProfile.length === 0}
		{@render empty()}
	{:else}
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.analytics_col_site()}</th>
						<th>{m.analytics_col_profile()}</th>
						<th style="text-align: right">{m.analytics_col_turns()}</th>
						<th style="text-align: right">{m.analytics_col_rejections()}</th>
						<th style="text-align: right">{m.analytics_col_llm_calls()}</th>
						<th style="text-align: right">{m.analytics_col_tokens()}</th>
						<th style="text-align: right">{m.analytics_col_cost()}</th>
						<th style="text-align: right">{m.analytics_col_errors()}</th>
					</tr>
				</thead>
				<tbody>
					{#each bySiteProfile as r (`${r.site}|${r.profile_id}`)}
						<tr>
							<td data-label={m.analytics_col_site()}>{@render siteCell(r.site)}</td>
							<td data-label={m.analytics_col_profile()}>{profileLabel(r.profile_id)}</td>
							<td data-label={m.analytics_col_turns()} class="text-right tabular-nums"
								>{num(r.turns)}</td
							>
							<td data-label={m.analytics_col_rejections()} class="text-right tabular-nums"
								>{num(r.rejections)}</td
							>
							<td data-label={m.analytics_col_llm_calls()} class="text-right tabular-nums"
								>{num(r.llm_calls)}</td
							>
							<td data-label={m.analytics_col_tokens()} class="text-right tabular-nums"
								>{compact(r.input_tokens + r.output_tokens)}</td
							>
							<td data-label={m.analytics_col_cost()} class="text-right tabular-nums"
								>{usd(r.cost_usd)}</td
							>
							<td
								data-label={m.analytics_col_errors()}
								class="text-right tabular-nums"
								style={r.llm_errors > 0 ? 'color: var(--color-danger);' : ''}
								>{num(r.llm_errors)}</td
							>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</section>

<section class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
	<h2 class="px-4 pt-3 pb-2 text-base font-semibold">{m.analytics_by_model_title()}</h2>
	{#if byModel.length === 0}
		{@render empty()}
	{:else}
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.analytics_col_model()}</th>
						<th style="text-align: right">{m.analytics_col_llm_calls()}</th>
						<th style="text-align: right">{m.analytics_col_input_tokens()}</th>
						<th style="text-align: right">{m.analytics_col_output_tokens()}</th>
						<th style="text-align: right">{m.analytics_col_cost()}</th>
						<th style="text-align: right">{m.analytics_col_errors()}</th>
						<th style="text-align: right">{m.analytics_col_error_rate()}</th>
					</tr>
				</thead>
				<tbody>
					{#each byModel as r (r.model)}
						<tr>
							<td data-label={m.analytics_col_model()} class="font-mono text-xs">{r.model}</td>
							<td data-label={m.analytics_col_llm_calls()} class="text-right tabular-nums"
								>{num(r.llm_calls)}</td
							>
							<td data-label={m.analytics_col_input_tokens()} class="text-right tabular-nums"
								>{num(r.input_tokens)}</td
							>
							<td data-label={m.analytics_col_output_tokens()} class="text-right tabular-nums"
								>{num(r.output_tokens)}</td
							>
							<td data-label={m.analytics_col_cost()} class="text-right tabular-nums"
								>{usd(r.cost_usd)}</td
							>
							<td
								data-label={m.analytics_col_errors()}
								class="text-right tabular-nums"
								style={r.llm_errors > 0 ? 'color: var(--color-danger);' : ''}
								>{num(r.llm_errors)}</td
							>
							<td data-label={m.analytics_col_error_rate()} class="text-right tabular-nums"
								>{pct(r.error_rate)}</td
							>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
	<p class="px-4 pb-3 text-xs text-[var(--color-text-muted)]">{m.analytics_hint_lower_bound()}</p>
</section>
