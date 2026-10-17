<script lang="ts">
	/** One user's usage: KPIs, a daily chart, breakdowns, top pages/API routes, errors,
	 *  IP addresses and the activity log. Devices (sign-in sessions) are pacod-id's: a link. */
	import { page } from '$app/state';
	import { m } from '$lib/paraglide/messages';
	import { getLocale } from '$lib/paraglide/runtime';
	import { ArrowLeft, UserRound } from 'lucide-svelte';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { profilesQuery, shortNameFor } from '$lib/features/profiles/queries';
	import { createAnalyticsUserQuery } from '$lib/features/analytics/queries';
	import {
		rangeById,
		bucketFor,
		fillSeries,
		bucketLabel,
		num,
		compact,
		fmtDuration,
		fmtDateTime,
		siteLabel,
		COLORS
	} from '$lib/features/analytics/format';
	import FilterBar from '$lib/features/analytics/FilterBar.svelte';
	import KpiGrid from '$lib/features/analytics/KpiGrid.svelte';
	import BreakdownTables from '$lib/features/analytics/BreakdownTables.svelte';
	import DataNotes from '$lib/features/analytics/DataNotes.svelte';
	import TimeSeriesChart from '$lib/features/analytics/TimeSeriesChart.svelte';
	import UserIpActivity from '$lib/features/analytics/UserIpActivity.svelte';
	import GatewayConsoleLink from '$lib/components/gateway/GatewayConsoleLink.svelte';
	import GatewayErrorNotice from '$lib/components/gateway/GatewayErrorNotice.svelte';
	import type { PageData } from './$types';

	let { data: pageData }: { data: PageData } = $props();
	const userId = $derived(pageData.userId);

	// ----- Filters (seeded from the overview's link)
	const initial = page.url.searchParams;
	let rangeId = $state(rangeById(initial.get('range') ?? '30d').id);
	let site = $state(initial.get('site') ?? '');
	let profile = $state(initial.get('profile') ?? '');

	const range = $derived(rangeById(rangeId));
	const bucket = $derived(bucketFor(range));
	const scopedProfileId = $derived(selectedProfile.id);
	const effectiveProfileId = $derived(scopedProfileId ?? (profile || undefined));

	// ----- Profiles → readable labels
	const profilesQ = profilesQuery();
	const locale = getLocale();
	const profiles = $derived($profilesQ.data ?? []);
	const profileOptions = $derived(
		profiles.map((p) => ({ id: p.id, label: shortNameFor(p, locale) }))
	);
	function profileLabel(id: string): string {
		const p = profiles.find((x) => x.id === id);
		return p ? shortNameFor(p, locale) : id;
	}

	const detail = $derived(
		createAnalyticsUserQuery(userId, {
			since_hours: range.hours,
			bucket,
			site: site || undefined,
			profile_id: effectiveProfileId
		})
	);
	const d = $derived($detail.data);
	const notFound = $derived(
		$detail.isError && (($detail.error as { status?: number } | null)?.status ?? 0) === 404
	);

	const backHref = $derived.by(() => {
		const p = new URLSearchParams();
		p.set('range', rangeId);
		if (site) p.set('site', site);
		if (profile && !scopedProfileId) p.set('profile', profile);
		return `/analytics?${p}`;
	});

	// ----- Activity chart
	const points = $derived(d ? fillSeries(d.series, d.since, bucket) : []);
	const labels = $derived(points.map((p) => bucketLabel(p.bucket, bucket)));
	const series = $derived([
		{
			label: m.analytics_col_api_requests(),
			data: points.map((p) => p.api_requests),
			color: COLORS.api
		},
		{ label: m.analytics_col_turns(), data: points.map((p) => p.turns), color: COLORS.turns },
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
	]);

	const title = $derived(d?.user.email ?? d?.user.display_name ?? userId);
</script>

<svelte:head><title>{m.analytics_user_title({ user: title })}</title></svelte:head>

<div class="space-y-4">
	<a
		href={backHref}
		class="inline-flex items-center gap-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
	>
		<ArrowLeft size={14} />
		{m.analytics_back()}
	</a>

	{#if notFound}
		<div class="rounded-xl border p-6 text-sm" style="border-color: var(--color-border);">
			{m.analytics_user_not_found()}
			<span class="font-mono text-xs text-[var(--color-text-muted)]">{userId}</span>
		</div>
	{:else}
		<!-- User header -->
		<div class="rounded-xl border p-4" style="border-color: var(--color-border);">
			<div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
				<h1 class="flex items-center gap-2 text-xl font-semibold">
					<UserRound size={20} />
					{title}
				</h1>
				<!-- This person's end-user page in the gateway console (new tab). -->
				<GatewayConsoleLink href={d?.console_url} class="mt-1.5" />
			</div>
			{#if d}
				{#if d.user.email && d.user.display_name}
					<p class="text-sm text-[var(--color-text-muted)]">{d.user.display_name}</p>
				{/if}
				<dl class="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-3 xl:grid-cols-6">
					<div>
						<dt class="field-label !mb-0.5">{m.analytics_user_roles()}</dt>
						<dd>
							{#if d.user.roles.length === 0}
								<span class="text-[var(--color-text-muted)]">—</span>
							{:else}
								<div class="flex flex-wrap gap-1">
									{#each d.user.roles as r (r)}
										<span
											class="rounded px-1.5 py-0.5 text-xs"
											style="background: var(--color-surface-3);">{r}</span
										>
									{/each}
								</div>
							{/if}
						</dd>
					</div>
					<div>
						<dt class="field-label !mb-0.5">{m.common_status()}</dt>
						<dd>
							{#if d.user.is_active === false}
								<span style="color: var(--color-danger);">{m.common_inactive_lc()}</span>
							{:else if d.user.is_active}
								<span style="color: var(--color-success, #15803d);">{m.common_active_lc()}</span>
							{:else}
								<span class="text-[var(--color-text-muted)]">—</span>
							{/if}
						</dd>
					</div>
					<div>
						<dt class="field-label !mb-0.5">{m.analytics_user_created()}</dt>
						<dd class="tabular-nums">{fmtDateTime(d.user.created_at)}</dd>
					</div>
					<div>
						<dt class="field-label !mb-0.5">{m.analytics_user_last_login()}</dt>
						<dd class="tabular-nums">{fmtDateTime(d.user.last_login_at)}</dd>
					</div>
					<div>
						<dt class="field-label !mb-0.5">{m.analytics_col_last_seen()}</dt>
						<dd class="tabular-nums">{fmtDateTime(d.user.last_seen)}</dd>
					</div>
					<div>
						<dt class="field-label !mb-0.5">{m.analytics_user_id()}</dt>
						<dd class="truncate font-mono text-xs" title={d.user.user_id}>{d.user.user_id}</dd>
					</div>
				</dl>
			{/if}
		</div>

		<FilterBar
			bind:rangeId
			bind:site
			bind:profile
			{scopedProfileId}
			{profileOptions}
			{profileLabel}
		/>
		<DataNotes profileActive={!!effectiveProfileId} />
		<GatewayErrorNotice error={d?.gateway_error} />

		{#if $detail.isError}
			<p class="text-sm" style="color: var(--color-danger);">{m.analytics_load_failed()}</p>
		{/if}

		<KpiGrid totals={d?.totals} errorsByType={d?.errors_by_type} loading={$detail.isPending} />

		<section class="rounded-xl border p-4" style="border-color: var(--color-border);">
			<h2 class="mb-3 text-sm font-semibold">{m.analytics_chart_activity()}</h2>
			{#if !d || d.series.length === 0}
				<p class="py-16 text-center text-sm text-[var(--color-text-muted)]">
					{$detail.isPending ? m.common_loading() : m.analytics_no_data()}
				</p>
			{:else}
				<TimeSeriesChart {labels} {series} format={(v) => compact(v)} height={240} />
			{/if}
		</section>

		<BreakdownTables data={d} loading={$detail.isPending} {profileLabel} />

		<div class="grid grid-cols-1 gap-4 xl:grid-cols-2">
			<!-- Top pages -->
			<section class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
				<h2 class="px-4 pt-3 pb-2 text-base font-semibold">{m.analytics_top_pages_title()}</h2>
				{#if !d || d.top_pages.length === 0}
					<p class="p-4 text-sm text-[var(--color-text-muted)]">{m.analytics_top_pages_empty()}</p>
				{:else}
					<div class="overflow-x-auto">
						<table class="data-table data-table-stack">
							<thead>
								<tr>
									<th>{m.analytics_col_route()}</th>
									<th style="text-align: right">{m.analytics_col_page_views()}</th>
									<th style="text-align: right">{m.analytics_col_dwell()}</th>
									<th style="text-align: right">{m.analytics_col_avg_dwell()}</th>
								</tr>
							</thead>
							<tbody>
								{#each d.top_pages as p, i (i)}
									<tr>
										<td data-label={m.analytics_col_route()} class="font-mono text-xs">{p.route}</td
										>
										<td data-label={m.analytics_col_page_views()} class="text-right tabular-nums"
											>{num(p.views)}</td
										>
										<td data-label={m.analytics_col_dwell()} class="text-right tabular-nums"
											>{fmtDuration(p.dwell_ms)}</td
										>
										<td data-label={m.analytics_col_avg_dwell()} class="text-right tabular-nums"
											>{fmtDuration(p.avg_dwell_ms)}</td
										>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</section>

			<!-- Top API routes -->
			<section class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
				<h2 class="px-4 pt-3 pb-2 text-base font-semibold">{m.analytics_top_api_title()}</h2>
				{#if !d || d.top_api_routes.length === 0}
					<p class="p-4 text-sm text-[var(--color-text-muted)]">{m.analytics_no_data()}</p>
				{:else}
					<div class="overflow-x-auto">
						<table class="data-table data-table-stack">
							<thead>
								<tr>
									<th>{m.analytics_col_route()}</th>
									<th style="text-align: right">{m.analytics_col_requests()}</th>
									<th style="text-align: right">{m.analytics_col_avg_duration()}</th>
									<th style="text-align: right">{m.analytics_col_http_errors()}</th>
								</tr>
							</thead>
							<tbody>
								{#each d.top_api_routes as r, i (i)}
									<tr>
										<td data-label={m.analytics_col_route()} class="font-mono text-xs">{r.route}</td
										>
										<td data-label={m.analytics_col_requests()} class="text-right tabular-nums"
											>{num(r.requests)}</td
										>
										<td data-label={m.analytics_col_avg_duration()} class="text-right tabular-nums"
											>{fmtDuration(r.avg_duration_ms)}</td
										>
										<td
											data-label={m.analytics_col_http_errors()}
											class="text-right tabular-nums"
											style={r.errors > 0 ? 'color: var(--color-danger);' : ''}>{num(r.errors)}</td
										>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</section>
		</div>

		<!-- Recent LLM errors -->
		<section class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
			<div class="flex flex-wrap items-baseline justify-between gap-x-4 px-4 pt-3 pb-2">
				<h2 class="text-base font-semibold">{m.analytics_recent_errors_title()}</h2>
				<GatewayConsoleLink href={d?.errors_console_url} label={m.gateway_console_open_errors()} />
			</div>
			{#if !d || d.recent_errors.length === 0}
				<p class="p-4 text-sm text-[var(--color-text-muted)]">
					{m.analytics_recent_errors_empty()}
				</p>
			{:else}
				<div class="overflow-x-auto">
					<table class="data-table data-table-stack">
						<thead>
							<tr>
								<th>{m.analytics_col_time()}</th>
								<th>{m.analytics_col_site()}</th>
								<th>{m.analytics_col_profile()}</th>
								<th>{m.analytics_col_model()}</th>
								<th>{m.analytics_col_purpose()}</th>
								<th>{m.analytics_col_error_type()}</th>
								<th>{m.analytics_col_message()}</th>
							</tr>
						</thead>
						<tbody>
							{#each d.recent_errors as e, i (i)}
								<tr>
									<td
										data-label={m.analytics_col_time()}
										class="text-xs whitespace-nowrap text-[var(--color-text-muted)] tabular-nums"
										>{fmtDateTime(e.occurred_at)}</td
									>
									<td data-label={m.analytics_col_site()}>{e.site ? siteLabel(e.site) : '—'}</td>
									<td data-label={m.analytics_col_profile()}
										>{e.profile_id ? profileLabel(e.profile_id) : '—'}</td
									>
									<td data-label={m.analytics_col_model()} class="font-mono text-xs">{e.model}</td>
									<td data-label={m.analytics_col_purpose()} class="text-xs">{e.purpose}</td>
									<td data-label={m.analytics_col_error_type()} style="color: var(--color-danger);">
										{e.error_type}{e.status_code ? ` (${e.status_code})` : ''}
									</td>
									<td
										data-label={m.analytics_col_message()}
										class="stack-block max-w-[360px] truncate text-xs"
										title={e.message ?? ''}>{e.message ?? '—'}</td
									>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>

		<!-- Devices: sign-in sessions live at pacod-id now -->
		<section class="rounded-xl border p-4" style="border-color: var(--color-border);">
			<h2 class="pb-1 text-base font-semibold">{m.analytics_devices_title()}</h2>
			<p class="text-sm text-[var(--color-text-muted)]">
				{m.analytics_devices_at_id()}
				<a
					href={pageData.idAdminUrl}
					target="_blank"
					rel="noopener noreferrer"
					class="underline"
					style="color: var(--color-brand);">{m.analytics_devices_open_id_admin()}</a
				>
			</p>
		</section>

		<UserIpActivity {userId} {profileLabel} />
	{/if}
</div>
