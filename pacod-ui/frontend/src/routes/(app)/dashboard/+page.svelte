<script lang="ts">
	import {
		Database,
		Users,
		MessageSquare,
		ArrowRight,
		ShieldCheck,
		Bug,
		BadgeCheck,
		FileStack,
		Coins,
		UserCheck,
		Activity,
		FolderOpen
	} from 'lucide-svelte';
	import {
		createQuotaConfigQuery,
		quotaConfigMutation,
		QUOTA_PERIODS,
		type QuotaConfigPatch
	} from '$lib/features/quota-config/queries';
	import { createDebugConfigQuery, debugConfigMutation } from '$lib/features/debug-config/queries';
	import { createLLMConfigQuery, updateLLMConfigMutation } from '$lib/features/llm-config/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { createDashboardStatsQuery } from '$lib/features/dashboard-stats/queries';
	import DailyUsageCard from '$lib/components/dashboard/DailyUsageCard.svelte';
	import ActivityCard from '$lib/components/dashboard/ActivityCard.svelte';
	import RecentConversations from '$lib/components/dashboard/RecentConversations.svelte';
	import SubscriptionCard from '$lib/components/dashboard/SubscriptionCard.svelte';
	import ComingSoonCard from '$lib/components/dashboard/ComingSoonCard.svelte';
	import GatewayErrorNotice from '$lib/components/gateway/GatewayErrorNotice.svelte';
	import { userLabel, hasRole } from '$lib/types/auth';
	import { m } from '$lib/paraglide/messages';

	let { data } = $props();

	const isAdmin = $derived(hasRole(data.user, 'admin'));
	const firstName = $derived(userLabel(data.user).split(' ')[0]);

	// ---- Admin-only platform widgets ----------------------------------------
	const stats = createDashboardStatsQuery();

	// Compact number formatter: 1234 → 1.2K, 1200000 → 1.2M.
	const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
	const fmtNum = (n: number) => compact.format(n);
	const fmtCost = (n: number) => (n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(n >= 0.01 ? 3 : 4)}`);

	const statCards = $derived(
		$stats.data
			? [
					{
						label: m.dashboard_stat_indexed_sources(),
						icon: FileStack,
						value: $stats.data.sources_total.toLocaleString(),
						sub: m.dashboard_stat_chunks({ count: $stats.data.chunks_total.toLocaleString() }),
						color: 'var(--color-brand)',
						bg: 'var(--color-brand-soft)'
					},
					{
						label: m.dashboard_stat_tokens_used(),
						icon: Coins,
						value: fmtNum($stats.data.tokens_total),
						sub: m.dashboard_stat_tokens_sub({ cost: fmtCost($stats.data.cost_usd_total) }),
						color: 'var(--color-gold)',
						bg: 'var(--color-gold-soft)'
					},
					{
						label: m.dashboard_stat_active_users(),
						icon: UserCheck,
						value: $stats.data.active_users_24h.toLocaleString(),
						sub: m.dashboard_stat_active_users_sub({
							now: $stats.data.active_users_5min,
							total: $stats.data.users_total
						}),
						color: 'var(--color-brand)',
						bg: 'var(--color-brand-soft)'
					},
					{
						label: m.dashboard_stat_answered_requests(),
						icon: Activity,
						value: $stats.data.requests_last_24h.toLocaleString(),
						sub: m.dashboard_stat_answered_requests_sub({
							perHour: $stats.data.requests_last_hour,
							per30d: $stats.data.requests_last_30d.toLocaleString()
						}),
						color: 'var(--color-gold)',
						bg: 'var(--color-gold-soft)'
					}
				]
			: []
	);

	const quotaConfig = createQuotaConfigQuery();
	const quotaConfigMut = quotaConfigMutation();

	function toggleQuotaEnforcement() {
		const current = $quotaConfig.data?.enforcement_enabled ?? true;
		$quotaConfigMut.mutate({ enforcement_enabled: !current });
	}

	function patchQuotaConfig(patch: QuotaConfigPatch) {
		$quotaConfigMut.mutate(patch);
	}

	function periodLabel(p: (typeof QUOTA_PERIODS)[number]): string {
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

	const debugConfig = createDebugConfigQuery();
	const debugConfigMut = debugConfigMutation();

	function toggleDebugAudit() {
		const current = $debugConfig.data?.enabled ?? false;
		$debugConfigMut.mutate(!current);
	}

	// Answer-verification pass — a per-profile runtime toggle, so it follows the
	// top-bar profile selector (null = the default agent). Reuses the LLM-config
	// endpoint that also backs the LLM & Search page.
	const verifCfg = $derived(createLLMConfigQuery(selectedProfile.id));
	const verifMut = $derived(updateLLMConfigMutation(selectedProfile.id));

	function toggleVerification() {
		const current = $verifCfg.data?.effective.verification_enabled ?? false;
		$verifMut.mutate({ verification_enabled: !current });
	}

	function toggleVerificationComplexOnly() {
		const current = $verifCfg.data?.effective.verification_complex_only ?? false;
		$verifMut.mutate({ verification_complex_only: !current });
	}

	const quickLinks = [
		{
			label: m.dashboard_quick_collections_label(),
			description: m.dashboard_quick_collections_desc(),
			icon: Database,
			href: '/collections',
			color: 'var(--color-brand)',
			bg: 'var(--color-brand-soft)'
		},
		{
			label: m.dashboard_quick_users_label(),
			description: m.dashboard_quick_users_desc(),
			icon: Users,
			href: '/users',
			color: 'var(--color-brand)',
			bg: 'var(--color-brand-soft)'
		},
		{
			label: m.dashboard_quick_chat_label(),
			description: m.dashboard_quick_chat_desc(),
			icon: MessageSquare,
			href: '/chat',
			color: 'var(--color-gold)',
			bg: 'var(--color-gold-soft)'
		}
	];
</script>

<svelte:head><title>{m.dashboard_title()}</title></svelte:head>

<div class="space-y-8">
	<!-- Header -->
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">
			{m.dashboard_greeting({ name: firstName })}
		</h1>
		<p class="mt-1 text-sm text-[var(--color-text-muted)]">
			{isAdmin ? m.dashboard_subtitle_admin() : m.dashboard_subtitle_user()}
		</p>
	</div>

	{#if isAdmin}
		<!-- ============================ ADMIN ============================ -->
		<!-- Platform overview -->
		<div>
			<h2 class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
				{m.dashboard_platform_overview()}
			</h2>
			{#if $stats.isPending}
				<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
					{#each Array(4) as _}
						<div
							class="h-28 animate-pulse rounded-xl border"
							style="background: var(--color-surface-2); border-color: var(--color-border);"
						></div>
					{/each}
				</div>
			{:else if $stats.isError}
				<div
					class="rounded-xl border px-5 py-4 text-sm text-[var(--color-text-muted)]"
					style="border-color: var(--color-border); background: var(--color-surface-2);"
				>
					{m.dashboard_stats_unavailable()}
				</div>
			{:else}
				<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
					{#each statCards as card}
						{@const Icon = card.icon}
						<div
							class="rounded-xl border p-5"
							style="background: var(--color-surface-2); border-color: var(--color-border);"
						>
							<div class="flex items-center gap-2.5">
								<div
									class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
									style="background: {card.bg};"
								>
									<Icon size={18} style="color: {card.color};" />
								</div>
								<span class="text-xs font-medium text-[var(--color-text-muted)]">{card.label}</span>
							</div>
							<div class="mt-3 text-2xl font-semibold tabular-nums">{card.value}</div>
							<div class="mt-0.5 text-xs text-[var(--color-text-muted)]">{card.sub}</div>
						</div>
					{/each}
				</div>
				<!-- Token/cost figures come from the gateway; with this set they read 0. -->
				<GatewayErrorNotice error={$stats.data?.gateway_error} class="mt-3" />
			{/if}
		</div>

		<!-- Quick links -->
		<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each quickLinks as link (link.label)}
				{@const Icon = link.icon}
				<a
					href={link.href}
					class="group flex items-start gap-4 rounded-xl border p-5 transition-all duration-150 hover:-translate-y-0.5 hover:shadow-lg"
					style="background: var(--color-surface-2); border-color: var(--color-border);"
				>
					<div
						class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-transform duration-150 group-hover:scale-110"
						style="background: {link.bg};"
					>
						<Icon size={20} style="color: {link.color};" />
					</div>
					<div class="min-w-0 flex-1">
						<div class="flex items-center justify-between">
							<span class="font-medium">{link.label}</span>
							<ArrowRight
								size={14}
								class="text-[var(--color-text-muted)] transition-transform duration-150 group-hover:translate-x-0.5"
							/>
						</div>
						<p class="mt-0.5 text-xs text-[var(--color-text-muted)]">{link.description}</p>
					</div>
				</a>
			{/each}
		</div>

		<!-- Global quota enforcement toggle -->
		{#if $quotaConfig.data}
			{@const cfg = $quotaConfig.data}
			<div>
				<h2
					class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase"
				>
					{m.dashboard_quota_enforcement()}
				</h2>
				<div
					class="flex items-center justify-between gap-4 rounded-xl border p-5"
					style="background: var(--color-surface-2); border-color: var(--color-border);"
				>
					<div class="flex items-center gap-2.5">
						<div
							class="flex h-9 w-9 items-center justify-center rounded-xl"
							style="background: var(--color-brand-soft);"
						>
							<ShieldCheck size={18} style="color: var(--color-brand);" />
						</div>
						<div>
							<div class="text-sm font-medium">{m.dashboard_global_token_quota()}</div>
							<div class="text-xs text-[var(--color-text-muted)]">
								{#if cfg.env_kill_switch}
									{m.dashboard_quota_forced_off()}
								{:else if cfg.enforcement_enabled}
									{m.dashboard_quota_enforced_all()}
								{:else}
									{m.dashboard_quota_disabled()}
								{/if}
							</div>
						</div>
					</div>
					<label
						class="flex cursor-pointer items-center gap-2.5"
						class:opacity-50={cfg.env_kill_switch}
					>
						<span class="text-sm font-medium">
							{cfg.enforcement_enabled ? m.common_on() : m.common_off()}
						</span>
						<input
							type="checkbox"
							class="h-4 w-4 rounded"
							checked={cfg.enforcement_enabled}
							disabled={cfg.env_kill_switch || $quotaConfigMut.isPending}
							onchange={toggleQuotaEnforcement}
						/>
					</label>
				</div>
			</div>
		{/if}

		<!-- Quota types: token-based & request-based (limit + validity period each) -->
		{#if $quotaConfig.data && !$quotaConfig.data.env_kill_switch}
			{@const cfg = $quotaConfig.data}
			<div>
				<h2
					class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase"
				>
					{m.dashboard_quota_types()}
				</h2>
				<div
					class="flex flex-col gap-4 rounded-xl border p-5"
					class:opacity-50={!cfg.enforcement_enabled}
					style="background: var(--color-surface-2); border-color: var(--color-border);"
				>
					{#each [{ kind: 'token', enabled: cfg.token_enabled, limit: cfg.token_limit, period: cfg.token_period, label: m.dashboard_quota_token_type(), hint: m.dashboard_quota_token_hint() }, { kind: 'request', enabled: cfg.request_enabled, limit: cfg.request_limit, period: cfg.request_period, label: m.dashboard_quota_request_type(), hint: m.dashboard_quota_request_hint() }] as row (row.kind)}
						<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
							<label class="flex min-w-[12rem] cursor-pointer items-center gap-2.5">
								<input
									type="checkbox"
									class="h-4 w-4 rounded"
									checked={row.enabled}
									disabled={!cfg.enforcement_enabled || $quotaConfigMut.isPending}
									onchange={() =>
										patchQuotaConfig(
											row.kind === 'token'
												? { token_enabled: !row.enabled }
												: { request_enabled: !row.enabled }
										)}
								/>
								<span>
									<span class="block text-sm font-medium">{row.label}</span>
									<span class="block text-xs text-[var(--color-text-muted)]">{row.hint}</span>
								</span>
							</label>
							<label class="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
								{m.dashboard_quota_limit()}
								<input
									type="number"
									min="1"
									class="field-input w-28"
									value={row.limit}
									disabled={!cfg.enforcement_enabled || !row.enabled || $quotaConfigMut.isPending}
									onchange={(e) => {
										const v = Math.max(1, Math.floor(Number(e.currentTarget.value)));
										if (!Number.isFinite(v)) return;
										patchQuotaConfig(
											row.kind === 'token' ? { token_limit: v } : { request_limit: v }
										);
									}}
								/>
							</label>
							<label class="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
								{m.dashboard_quota_period()}
								<select
									class="field-input w-28"
									value={row.period}
									disabled={!cfg.enforcement_enabled || !row.enabled || $quotaConfigMut.isPending}
									onchange={(e) => {
										const v = e.currentTarget.value as QuotaConfigPatch['token_period'];
										patchQuotaConfig(
											row.kind === 'token' ? { token_period: v } : { request_period: v }
										);
									}}
								>
									{#each QUOTA_PERIODS as p (p)}
										<option value={p}>{periodLabel(p)}</option>
									{/each}
								</select>
							</label>
						</div>
					{/each}
				</div>
			</div>
		{/if}

		<!-- Debug audit toggle -->
		{#if $debugConfig.data}
			{@const dcfg = $debugConfig.data}
			<div>
				<h2
					class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase"
				>
					{m.dashboard_debug_logging()}
				</h2>
				<div
					class="flex items-center justify-between gap-4 rounded-xl border p-5"
					style="background: var(--color-surface-2); border-color: var(--color-border);"
				>
					<div class="flex items-center gap-2.5">
						<div
							class="flex h-9 w-9 items-center justify-center rounded-xl"
							style="background: var(--color-brand-soft);"
						>
							<Bug size={18} style="color: var(--color-brand);" />
						</div>
						<div>
							<div class="text-sm font-medium">{m.dashboard_debug_audit_title()}</div>
							<div class="text-xs text-[var(--color-text-muted)]">
								{#if dcfg.enabled}
									{m.dashboard_debug_recording()}
								{:else}
									{m.dashboard_debug_off()}
								{/if}
							</div>
						</div>
					</div>
					<label class="flex cursor-pointer items-center gap-2.5">
						<span class="text-sm font-medium">
							{dcfg.enabled ? m.common_on() : m.common_off()}
						</span>
						<input
							type="checkbox"
							class="h-4 w-4 rounded"
							checked={dcfg.enabled}
							disabled={$debugConfigMut.isPending}
							onchange={toggleDebugAudit}
						/>
					</label>
				</div>
			</div>
		{/if}

		<!-- Answer verification toggle (per-profile — follows the top-bar selector) -->
		{#if $verifCfg.data}
			{@const vcfg = $verifCfg.data.effective}
			<div>
				<h2
					class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase"
				>
					{m.dashboard_verification()}
				</h2>
				<div
					class="rounded-xl border p-5"
					style="background: var(--color-surface-2); border-color: var(--color-border);"
				>
					<div class="flex items-center justify-between gap-4">
						<div class="flex items-center gap-2.5">
							<div
								class="flex h-9 w-9 items-center justify-center rounded-xl"
								style="background: var(--color-brand-soft);"
							>
								<BadgeCheck size={18} style="color: var(--color-brand);" />
							</div>
							<div>
								<div class="text-sm font-medium">{m.dashboard_verification_title()}</div>
								<div class="text-xs text-[var(--color-text-muted)]">
									{#if vcfg.verification_enabled}
										{vcfg.verification_complex_only
											? m.dashboard_verification_complex_on()
											: m.dashboard_verification_on()}
									{:else}
										{m.dashboard_verification_off()}
									{/if}
								</div>
							</div>
						</div>
						<label class="flex cursor-pointer items-center gap-2.5">
							<span class="text-sm font-medium">
								{vcfg.verification_enabled ? m.common_on() : m.common_off()}
							</span>
							<input
								type="checkbox"
								class="h-4 w-4 rounded"
								checked={vcfg.verification_enabled}
								disabled={$verifMut.isPending}
								onchange={toggleVerification}
							/>
						</label>
					</div>

					{#if vcfg.verification_enabled}
						<label
							class="mt-4 flex cursor-pointer items-center gap-2 border-t pt-3 text-xs text-[var(--color-text-muted)]"
							style="border-color: var(--color-border);"
						>
							<input
								type="checkbox"
								class="h-3.5 w-3.5 rounded"
								checked={vcfg.verification_complex_only}
								disabled={$verifMut.isPending}
								onchange={toggleVerificationComplexOnly}
							/>
							{m.dashboard_verification_complex_only()}
						</label>
					{/if}

					<p class="mt-3 text-xs text-[var(--color-text-muted)]">
						{m.dashboard_verification_hint()}
					</p>
				</div>
			</div>
		{/if}

		<!-- Daily usage -->
		<div>
			<h2 class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
				{m.dashboard_daily_usage()}
			</h2>
			<DailyUsageCard />
		</div>
	{:else}
		<!-- ============================ USER ============================= -->
		<!-- Daily usage -->
		<div>
			<h2 class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
				{m.dashboard_daily_usage()}
			</h2>
			<DailyUsageCard />
		</div>

		<!-- Activity -->
		<div>
			<h2 class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
				{m.dashboard_activity()}
			</h2>
			<ActivityCard />
		</div>

		<!-- Recent conversations + new placeholder section -->
		<div class="mb-1 grid gap-6 lg:grid-cols-2">
			<RecentConversations />
			<div>
				<h2
					class="mb-3 text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase"
				>
					{m.dashboard_documents()}
				</h2>
				<ComingSoonCard
					title={m.dashboard_doc_vault_title()}
					description={m.dashboard_doc_vault_desc()}
					icon={FolderOpen}
				/>
			</div>
		</div>

		<!-- Subscription (demo) -->
		<SubscriptionCard />
	{/if}
</div>
