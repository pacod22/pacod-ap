<script lang="ts">
	import { Check, CreditCard, Sparkles, Crown } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';
	import { createQuotaQuery } from '$lib/features/quota/queries';

	// Real per-user usage comes from the request-based quota (GET /api/v1/quota).
	// The plan comparison below stays demo billing until a provider is wired up.
	const quota = createQuotaQuery();

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

	// When the admin has enabled the request quota, show real used/limit; otherwise
	// the user is not request-capped, so present it as unlimited.
	const requestEnabled = $derived($quota.data?.request_enabled ?? false);
	const requestUsed = $derived($quota.data?.request_used ?? 0);
	const requestLimit = $derived($quota.data?.request_limit ?? 0);
	const requestPeriod = $derived($quota.data?.request_period ?? 'month');
	const usagePercent = $derived(
		requestEnabled && requestLimit > 0 ? Math.min(100, (requestUsed / requestLimit) * 100) : 0
	);

	type Plan = {
		id: string;
		name: string;
		price: string;
		cadence: string;
		features: string[];
		highlight?: boolean;
	};

	const currentPlanId = 'free';

	const plans: Plan[] = [
		{
			id: 'free',
			name: 'Free',
			price: '€0',
			cadence: m.subscription_cadence_mo(),
			features: [
				m.subscription_feat_free_1(),
				m.subscription_feat_free_2(),
				m.subscription_feat_free_3()
			]
		},
		{
			id: 'pro',
			name: 'Pro',
			price: '€9',
			cadence: m.subscription_cadence_mo(),
			highlight: true,
			features: [
				m.subscription_feat_pro_1(),
				m.subscription_feat_pro_2(),
				m.subscription_feat_pro_3(),
				m.subscription_feat_pro_4()
			]
		},
		{
			id: 'business',
			name: 'Business',
			price: '€29',
			cadence: m.subscription_cadence_mo(),
			features: [
				m.subscription_feat_business_1(),
				m.subscription_feat_business_2(),
				m.subscription_feat_business_3(),
				m.subscription_feat_business_4()
			]
		}
	];

	const current = $derived(plans.find((p) => p.id === currentPlanId) ?? plans[0]);
	const renewalDate = new Date(Date.now() + 18 * 86_400_000).toLocaleDateString(undefined, {
		day: 'numeric',
		month: 'short',
		year: 'numeric'
	});
</script>

<div class="flex items-center justify-between">
	<h2 class="text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
		{m.subscription_heading()}
	</h2>
	<span
		class="rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase"
		style="background: var(--color-gold-soft); color: var(--color-gold);"
	>
		{m.common_coming_soon()}
	</span>
</div>

<div
	class="mt-3 overflow-hidden rounded-xl border"
	style="background: var(--color-surface-2); border-color: var(--color-border);"
>
	<!-- Current plan -->
	<div class="flex flex-wrap items-center justify-between gap-4 p-6">
		<div class="flex items-center gap-3">
			<div
				class="flex h-11 w-11 items-center justify-center rounded-xl"
				style="background: var(--color-brand-soft);"
			>
				<Crown size={20} style="color: var(--color-brand);" />
			</div>
			<div>
				<div class="flex items-center gap-2">
					<span class="text-base font-semibold"
						>{m.subscription_plan_label({ name: current.name })}</span
					>
					<span
						class="rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase"
						style="background: var(--color-success-soft, var(--color-brand-soft)); color: var(--color-success, var(--color-brand));"
					>
						{m.common_active_cap()}
					</span>
				</div>
				<div class="mt-0.5 text-xs text-[var(--color-text-muted)]">
					{m.subscription_renews({
						price: current.price,
						cadence: current.cadence,
						date: renewalDate
					})}
				</div>
			</div>
		</div>
		<button
			type="button"
			disabled
			title={m.subscription_manage_billing_title()}
			class="flex cursor-not-allowed items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium opacity-60"
			style="border-color: var(--color-border); background: var(--color-surface);"
		>
			<CreditCard size={15} />
			{m.subscription_manage_billing()}
		</button>
	</div>

	<!-- Usage against plan — real request-quota data (GET /api/v1/quota) -->
	<div class="border-t px-6 py-4" style="border-color: var(--color-border);">
		<div class="mb-2 flex items-center justify-between text-xs">
			<span class="text-[var(--color-text-muted)]">
				{m.subscription_requests_usage({ period: periodLabel(requestPeriod) })}
			</span>
			<span class="font-medium tabular-nums">
				{#if $quota.isPending}
					…
				{:else if requestEnabled}
					{requestUsed.toLocaleString()} / {requestLimit.toLocaleString()}
				{:else}
					{requestUsed.toLocaleString()} / {m.subscription_unlimited()}
				{/if}
			</span>
		</div>
		<div class="h-2 overflow-hidden rounded-full" style="background: var(--color-surface-3);">
			<div
				class="h-full rounded-full transition-all duration-500"
				style="width: {requestEnabled ? usagePercent : 0}%; background: {usagePercent >= 90
					? 'var(--color-danger)'
					: usagePercent >= 70
						? 'var(--color-warning)'
						: 'var(--color-brand)'};"
			></div>
		</div>
	</div>

	<!-- Plan comparison / upgrade -->
	<div
		class="grid gap-px border-t sm:grid-cols-3"
		style="border-color: var(--color-border); background: var(--color-border);"
	>
		{#each plans as plan (plan.id)}
			{@const isCurrent = plan.id === currentPlanId}
			<div class="flex flex-col p-5" style="background: var(--color-surface-2);">
				<div class="flex items-center gap-1.5">
					{#if plan.highlight}
						<Sparkles size={14} style="color: var(--color-gold);" />
					{/if}
					<span class="text-sm font-semibold">{plan.name}</span>
				</div>
				<div class="mt-1">
					<span class="text-xl font-semibold tabular-nums">{plan.price}</span>
					<span class="text-xs text-[var(--color-text-muted)]">{plan.cadence}</span>
				</div>
				<ul class="mt-3 flex-1 space-y-1.5">
					{#each plan.features as feature}
						<li class="flex items-start gap-1.5 text-xs text-[var(--color-text-muted)]">
							<Check size={13} class="mt-0.5 shrink-0 text-[var(--color-brand)]" />
							<span>{feature}</span>
						</li>
					{/each}
				</ul>
				<button
					type="button"
					disabled
					title={m.subscription_upgrade_disabled_title()}
					class="mt-4 cursor-not-allowed rounded-lg px-3 py-2 text-sm font-medium opacity-70"
					style={isCurrent
						? 'background: var(--color-surface-3); color: var(--color-text-muted);'
						: 'background: var(--color-brand); color: white;'}
				>
					{isCurrent
						? m.subscription_current_plan()
						: m.subscription_upgrade_to({ name: plan.name })}
				</button>
			</div>
		{/each}
	</div>
</div>
