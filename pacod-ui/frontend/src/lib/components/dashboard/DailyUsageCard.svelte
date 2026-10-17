<script lang="ts">
	import { Zap, TrendingUp } from 'lucide-svelte';
	import { createQuotaQuery } from '$lib/features/quota/queries';
	import { m } from '$lib/paraglide/messages';

	// Self-contained: TanStack Query dedupes by key, so multiple instances
	// (dashboard + account) share one fetch.
	const quota = createQuotaQuery();

	const quotaPercent = $derived(
		$quota.data && $quota.data.daily_tokens_limit > 0
			? Math.min(100, ($quota.data.daily_tokens_used / $quota.data.daily_tokens_limit) * 100)
			: 0
	);

	const quotaColor = $derived(
		quotaPercent >= 90
			? 'var(--color-danger)'
			: quotaPercent >= 70
				? 'var(--color-warning)'
				: 'var(--color-brand)'
	);

	const requestPercent = $derived(
		$quota.data && $quota.data.request_limit > 0
			? Math.min(100, ($quota.data.request_used / $quota.data.request_limit) * 100)
			: 0
	);

	function periodLabel(p: string): string {
		switch (p) {
			case 'hour':
				return m.quota_period_hour();
			case 'week':
				return m.quota_period_week();
			case 'month':
				return m.quota_period_month();
			default:
				return m.quota_period_day();
		}
	}
</script>

{#if $quota.isPending}
	<div
		class="flex h-36 items-center justify-center rounded-xl border text-sm text-[var(--color-text-muted)]"
		style="border-color: var(--color-border); background: var(--color-surface-2);"
	>
		{m.dashboard_quota_loading()}
	</div>
{:else if $quota.data}
	{@const q = $quota.data}
	<div
		class="rounded-xl border p-6"
		style="background: var(--color-surface-2); border-color: var(--color-border);"
	>
		<!-- Header -->
		<div class="mb-4 flex items-center justify-between">
			<div class="flex items-center gap-2.5">
				<div
					class="flex h-9 w-9 items-center justify-center rounded-xl"
					style="background: var(--color-brand-soft);"
				>
					<Zap size={18} style="color: var(--color-brand);" />
				</div>
				<div>
					<div class="text-sm font-medium">{m.dashboard_token_budget()}</div>
					<div class="text-xs text-[var(--color-text-muted)]">
						{m.dashboard_token_budget_reset()}
					</div>
				</div>
			</div>
			<div class="text-right">
				<div class="text-xl font-semibold tabular-nums">
					{q.daily_tokens_used.toLocaleString()}
				</div>
				<div class="text-xs text-[var(--color-text-muted)]">
					{m.dashboard_token_budget_of({ limit: q.daily_tokens_limit.toLocaleString() })}
				</div>
			</div>
		</div>

		<!-- Progress bar -->
		<div class="mb-4 h-2 overflow-hidden rounded-full" style="background: var(--color-surface-3);">
			<div
				class="h-full rounded-full transition-all duration-500"
				style="width: {quotaPercent}%; background: {quotaColor};"
			></div>
		</div>

		<!-- Stats row -->
		<div class="grid grid-cols-3 divide-x divide-[var(--color-border)]">
			<div class="pr-4">
				<div class="flex items-center gap-1.5">
					<TrendingUp size={12} class="text-[var(--color-text-muted)]" />
					<span class="text-xs text-[var(--color-text-muted)]">{m.dashboard_token_remaining()}</span
					>
				</div>
				<div class="mt-1 text-sm font-semibold tabular-nums">
					{q.daily_tokens_remaining.toLocaleString()}
				</div>
			</div>
			<div class="px-4">
				<div class="text-xs text-[var(--color-text-muted)]">{m.dashboard_requests_per_min()}</div>
				<div class="mt-1 text-sm font-semibold tabular-nums">
					{q.requests_this_minute}
					<span class="text-xs font-normal text-[var(--color-text-muted)]"
						>/ {q.requests_per_minute_limit}</span
					>
				</div>
			</div>
			<div class="pl-4">
				<div class="text-xs text-[var(--color-text-muted)]">{m.dashboard_used_today()}</div>
				<div class="mt-1 text-sm font-semibold tabular-nums">
					{quotaPercent.toFixed(1)}%
				</div>
			</div>
		</div>

		<!-- Request-based quota (shown only when the admin has enabled it) -->
		{#if q.request_enabled}
			<div class="mt-4 border-t pt-4" style="border-color: var(--color-border);">
				<div class="mb-1.5 flex items-center justify-between text-xs">
					<span class="text-[var(--color-text-muted)]">
						{m.dashboard_request_quota({ period: periodLabel(q.request_period) })}
					</span>
					<span class="font-semibold tabular-nums">
						{q.request_used.toLocaleString()}
						<span class="font-normal text-[var(--color-text-muted)]"
							>/ {q.request_limit.toLocaleString()}</span
						>
					</span>
				</div>
				<div class="h-2 overflow-hidden rounded-full" style="background: var(--color-surface-3);">
					<div
						class="h-full rounded-full transition-all duration-500"
						style="width: {requestPercent}%; background: {requestPercent >= 90
							? 'var(--color-danger)'
							: requestPercent >= 70
								? 'var(--color-warning)'
								: 'var(--color-brand)'};"
					></div>
				</div>
			</div>
		{/if}
	</div>
{:else}
	<div
		class="rounded-xl border px-5 py-4 text-sm text-[var(--color-text-muted)]"
		style="border-color: var(--color-border); background: var(--color-surface-2);"
	>
		{m.dashboard_quota_unavailable()}
	</div>
{/if}
