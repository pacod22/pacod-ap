<script lang="ts">
	import { RefreshCw, Trash2, Check, X, ShieldCheck, ShieldAlert, HelpCircle } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		createRedisInfoQuery,
		createRedisQuotaQuery,
		createTopicCacheStatsQuery,
		createTopicCacheEntriesQuery,
		createConversationSummariesQuery,
		resetUserQuotaMutation,
		flushTopicCacheMutation,
		flushConversationSummariesMutation,
		type UserQuota,
		type TopicCacheEntry
	} from '$lib/features/redis/queries';
	import { m } from '$lib/paraglide/messages';

	const info = createRedisInfoQuery();
	const quotas = createRedisQuotaQuery();
	const cacheStats = createTopicCacheStatsQuery();
	const cacheEntries = createTopicCacheEntriesQuery();
	const summaries = createConversationSummariesQuery();
	const resetQuotaMut = resetUserQuotaMutation();
	const flushCacheMut = flushTopicCacheMutation();
	const flushSummariesMut = flushConversationSummariesMutation();

	let confirmResetUserId = $state<string | null>(null);
	let confirmFlushCache = $state(false);
	let confirmFlushSummaries = $state(false);
	let flushError = $state('');
	let flushSummariesError = $state('');

	async function resetQuota(userId: string) {
		try {
			await $resetQuotaMut.mutateAsync(userId);
			confirmResetUserId = null;
		} catch {
			// error surfaced via mutation state
		}
	}

	async function flushCache() {
		flushError = '';
		try {
			await $flushCacheMut.mutateAsync();
			confirmFlushCache = false;
		} catch (e: unknown) {
			flushError = e instanceof Error ? e.message : m.redis_flush_failed();
		}
	}

	async function flushSummaries() {
		flushSummariesError = '';
		try {
			await $flushSummariesMut.mutateAsync();
			confirmFlushSummaries = false;
		} catch (e: unknown) {
			flushSummariesError = e instanceof Error ? e.message : m.redis_flush_failed();
		}
	}

	function fmtUpdated(iso: string | null) {
		if (!iso) return '—';
		const d = new Date(iso);
		if (Number.isNaN(d.getTime())) return iso;
		return d.toLocaleString();
	}

	function fmtUptime(seconds: number) {
		const d = Math.floor(seconds / 86400);
		const h = Math.floor((seconds % 86400) / 3600);
		const m = Math.floor((seconds % 3600) / 60);
		const parts = [];
		if (d) parts.push(`${d}d`);
		if (h) parts.push(`${h}h`);
		parts.push(`${m}m`);
		return parts.join(' ');
	}

	function fmtTtl(seconds: number) {
		if (seconds < 0) return '∞';
		if (seconds < 60) return `${seconds}s`;
		if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
		const h = Math.floor(seconds / 3600);
		const m = Math.floor((seconds % 3600) / 60);
		return `${h}h ${m}m`;
	}

	function pct(quota: UserQuota) {
		if (quota.daily_tokens_limit === 0) return 0;
		return Math.min(100, Math.round((quota.daily_tokens_used / quota.daily_tokens_limit) * 100));
	}

	function verdictLabel(entry: TopicCacheEntry) {
		if (entry.is_illegal) return m.redis_verdict_illegal();
		if (!entry.is_relevant) return m.redis_verdict_offtopic();
		if (entry.needs_clarification) return m.redis_verdict_needs_clarification();
		return m.redis_verdict_relevant();
	}

	function verdictColor(entry: TopicCacheEntry) {
		if (entry.is_illegal) return 'var(--color-danger)';
		if (!entry.is_relevant) return 'var(--color-text-faint)';
		if (entry.needs_clarification) return 'var(--color-warning)';
		return 'var(--color-brand)';
	}
</script>

<svelte:head><title>{m.redis_title()}</title></svelte:head>

<div class="space-y-8">
	<!-- Header -->
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">{m.nav_redis()}</h1>
		<p class="mt-1 text-sm text-[var(--color-text-muted)]">
			{m.redis_intro()}
		</p>
	</div>

	<!-- Server info -->
	<section class="space-y-3">
		<h2 class="text-base font-semibold">{m.redis_server_info()}</h2>
		{#if $info.isPending}
			<p class="text-sm text-[var(--color-text-muted)]">{m.common_loading()}</p>
		{:else if $info.isError}
			<p class="text-sm" style="color: var(--color-danger);">{m.redis_load_server_failed()}</p>
		{:else if $info.data}
			{@const d = $info.data}
			<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
				{#each [{ label: m.redis_stat_version(), value: d.redis_version }, { label: m.redis_stat_uptime(), value: fmtUptime(d.uptime_in_seconds) }, { label: m.redis_stat_clients(), value: String(d.connected_clients) }, { label: m.redis_stat_memory_used(), value: d.used_memory_human }, { label: m.redis_stat_memory_peak(), value: d.used_memory_peak_human }, { label: m.redis_stat_commands(), value: d.total_commands_processed.toLocaleString() }, { label: m.redis_stat_cache_hits(), value: d.keyspace_hits.toLocaleString() }, { label: m.redis_stat_cache_misses(), value: d.keyspace_misses.toLocaleString() }] as stat}
					<div
						class="rounded-xl border p-4"
						style="background: var(--color-surface-2); border-color: var(--color-border);"
					>
						<p class="text-xs text-[var(--color-text-muted)]">{stat.label}</p>
						<p class="mt-1 text-lg font-semibold tabular-nums">{stat.value}</p>
					</div>
				{/each}
			</div>
		{/if}
	</section>

	<!-- Topic cache -->
	<section class="space-y-3">
		<div class="flex items-center justify-between">
			<h2 class="text-base font-semibold">{m.redis_topic_cache()}</h2>
			<div class="flex items-center gap-2">
				{#if confirmFlushCache}
					<span class="text-sm text-[var(--color-text-muted)]">{m.redis_flush_confirm()}</span>
					<button
						onclick={flushCache}
						class="flex items-center gap-1 rounded-lg px-2 py-1 text-sm transition-colors"
						style="color: var(--color-danger);"
						disabled={$flushCacheMut.isPending}
					>
						<Check size={14} />
						{m.redis_flush_yes()}
					</button>
					<button
						onclick={() => (confirmFlushCache = false)}
						class="flex items-center gap-1 rounded-lg px-2 py-1 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-2)]"
					>
						<X size={14} />
						{m.common_cancel()}
					</button>
				{:else}
					<Button variant="ghost" size="sm" onclick={() => (confirmFlushCache = true)}>
						<Trash2 size={14} />
						{m.redis_flush_cache()}
					</Button>
				{/if}
			</div>
		</div>

		{#if flushError}
			<p class="text-sm" style="color: var(--color-danger);">{flushError}</p>
		{/if}

		{#if $cacheStats.isPending}
			<p class="text-sm text-[var(--color-text-muted)]">{m.common_loading()}</p>
		{:else if $cacheStats.isError}
			<p class="text-sm" style="color: var(--color-danger);">{m.redis_load_cache_stats_failed()}</p>
		{:else if $cacheStats.data}
			<div
				class="inline-flex items-center gap-3 rounded-xl border px-5 py-4"
				style="background: var(--color-surface-2); border-color: var(--color-border);"
			>
				<span class="text-3xl font-bold tabular-nums">{$cacheStats.data.cached_entries}</span>
				<span class="text-sm text-[var(--color-text-muted)]"
					>{m.redis_cached_classifications()}</span
				>
			</div>
		{/if}

		<!-- Cache entries detail table -->
		{#if $cacheEntries.isPending}
			<p class="text-sm text-[var(--color-text-muted)]">{m.redis_loading_entries()}</p>
		{:else if $cacheEntries.isError}
			<p class="text-sm" style="color: var(--color-danger);">
				{m.redis_load_cache_entries_failed()}
			</p>
		{:else if ($cacheEntries.data?.length ?? 0) === 0}
			<p class="text-sm text-[var(--color-text-muted)]">{m.redis_no_cached_entries()}</p>
		{:else}
			<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
				<div class="overflow-x-auto">
					<table class="data-table data-table-stack">
						<thead>
							<tr>
								<th>{m.redis_col_key_prefix()}</th>
								<th>{m.redis_col_ttl()}</th>
								<th>{m.redis_col_verdict()}</th>
								<th>{m.redis_col_lang()}</th>
								<th>{m.redis_col_confidence()}</th>
								<th>{m.redis_col_reason()}</th>
							</tr>
						</thead>
						<tbody>
							{#each $cacheEntries.data! as entry (entry.key_suffix)}
								<tr>
									<td data-label={m.redis_col_key_prefix()} class="font-mono text-xs"
										>{entry.key_suffix}…</td
									>
									<td data-label={m.redis_col_ttl()} class="text-xs tabular-nums"
										>{fmtTtl(entry.ttl_seconds)}</td
									>
									<td data-label={m.redis_col_verdict()}>
										<span
											class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
											style="color: {verdictColor(
												entry
											)}; background: color-mix(in srgb, {verdictColor(entry)} 12%, transparent);"
										>
											{#if entry.is_illegal}
												<ShieldAlert size={11} />
											{:else if !entry.is_relevant}
												<X size={11} />
											{:else if entry.needs_clarification}
												<HelpCircle size={11} />
											{:else}
												<ShieldCheck size={11} />
											{/if}
											{verdictLabel(entry)}
										</span>
									</td>
									<td data-label={m.redis_col_lang()} class="text-xs uppercase"
										>{entry.detected_language}</td
									>
									<td data-label={m.redis_col_confidence()} class="text-xs tabular-nums"
										>{Math.round(entry.confidence * 100)}%</td
									>
									<td
										data-label={m.redis_col_reason()}
										class="stack-block max-w-xs truncate text-xs text-[var(--color-text-muted)]"
										title={entry.reason}
									>
										{entry.reason || '—'}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>
		{/if}
	</section>

	<!-- Conversation history -->
	<section class="space-y-3">
		<div class="flex items-center justify-between">
			<h2 class="text-base font-semibold">{m.redis_conversation_history()}</h2>
			<div class="flex items-center gap-2">
				{#if confirmFlushSummaries}
					<span class="text-sm text-[var(--color-text-muted)]"
						>{m.redis_flush_summaries_confirm()}</span
					>
					<button
						onclick={flushSummaries}
						class="flex items-center gap-1 rounded-lg px-2 py-1 text-sm transition-colors"
						style="color: var(--color-danger);"
						disabled={$flushSummariesMut.isPending}
					>
						<Check size={14} />
						{m.redis_flush_yes()}
					</button>
					<button
						onclick={() => (confirmFlushSummaries = false)}
						class="flex items-center gap-1 rounded-lg px-2 py-1 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-2)]"
					>
						<X size={14} />
						{m.common_cancel()}
					</button>
				{:else}
					<Button variant="ghost" size="sm" onclick={() => (confirmFlushSummaries = true)}>
						<Trash2 size={14} />
						{m.redis_flush_summaries()}
					</Button>
				{/if}
			</div>
		</div>

		<p class="text-xs text-[var(--color-text-muted)]">
			{m.redis_conversation_intro()}
		</p>

		{#if flushSummariesError}
			<p class="text-sm" style="color: var(--color-danger);">{flushSummariesError}</p>
		{/if}

		{#if $summaries.isPending}
			<p class="text-sm text-[var(--color-text-muted)]">{m.redis_loading_entries()}</p>
		{:else if $summaries.isError}
			<p class="text-sm" style="color: var(--color-danger);">{m.redis_load_summaries_failed()}</p>
		{:else if ($summaries.data?.length ?? 0) === 0}
			<p class="text-sm text-[var(--color-text-muted)]">{m.redis_no_summaries()}</p>
		{:else}
			<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
				<div class="overflow-x-auto">
					<table class="data-table data-table-stack">
						<thead>
							<tr>
								<th>{m.redis_col_profile()}</th>
								<th>{m.redis_col_session()}</th>
								<th>{m.redis_col_ttl()}</th>
								<th>{m.redis_col_folded()}</th>
								<th>{m.redis_col_lang()}</th>
								<th>{m.redis_col_updated()}</th>
								<th>{m.redis_col_summary()}</th>
							</tr>
						</thead>
						<tbody>
							{#each $summaries.data! as entry (entry.profile_id + '|' + entry.session_id)}
								<tr>
									<td data-label={m.redis_col_profile()} class="font-mono text-xs"
										>{entry.profile_id}</td
									>
									<td data-label={m.redis_col_session()} class="font-mono text-xs"
										>{entry.session_id}</td
									>
									<td data-label={m.redis_col_ttl()} class="text-xs tabular-nums"
										>{fmtTtl(entry.ttl_seconds)}</td
									>
									<td data-label={m.redis_col_folded()} class="text-xs tabular-nums"
										>{entry.covered_count}</td
									>
									<td data-label={m.redis_col_lang()} class="text-xs uppercase">{entry.language}</td
									>
									<td
										data-label={m.redis_col_updated()}
										class="text-xs text-[var(--color-text-muted)] tabular-nums"
										>{fmtUpdated(entry.updated_at)}</td
									>
									<td
										data-label={m.redis_col_summary()}
										class="stack-block max-w-md truncate text-xs text-[var(--color-text-muted)]"
										title={entry.summary_preview}
									>
										{entry.summary_preview || '—'}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>
			<p class="text-xs text-[var(--color-text-muted)]">
				{m.redis_summaries_count({ n: $summaries.data!.length })}
			</p>
		{/if}
	</section>

	<!-- User quotas -->
	<section class="space-y-3">
		<h2 class="text-base font-semibold">{m.redis_user_quotas()}</h2>

		{#if $quotas.isPending}
			<p class="text-sm text-[var(--color-text-muted)]">{m.common_loading()}</p>
		{:else if $quotas.isError}
			<p class="text-sm" style="color: var(--color-danger);">{m.redis_load_quotas_failed()}</p>
		{:else if ($quotas.data?.length ?? 0) === 0}
			<p class="text-sm text-[var(--color-text-muted)]">{m.redis_no_quota_counters()}</p>
		{:else}
			<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
				<div class="overflow-x-auto">
					<table class="data-table data-table-stack">
						<thead>
							<tr>
								<th>{m.redis_col_user_id()}</th>
								<th>{m.redis_col_daily_used()}</th>
								<th>{m.redis_col_daily_limit()}</th>
								<th>{m.redis_col_usage()}</th>
								<th>{m.redis_col_reqmin()}</th>
								<th>{m.redis_col_reqmin_limit()}</th>
								<th></th>
							</tr>
						</thead>
						<tbody>
							{#each $quotas.data! as quota (quota.user_id)}
								<tr>
									<td data-label={m.redis_col_user_id()} class="font-mono text-xs"
										>{quota.user_id}</td
									>
									<td data-label={m.redis_col_daily_used()} class="tabular-nums"
										>{quota.daily_tokens_used.toLocaleString()}</td
									>
									<td
										data-label={m.redis_col_daily_limit()}
										class="text-[var(--color-text-muted)] tabular-nums"
									>
										{quota.daily_tokens_limit.toLocaleString()}
									</td>
									<td data-label={m.redis_col_usage()} class="stack-block w-36">
										<div class="flex items-center gap-2">
											<div
												class="h-1.5 flex-1 overflow-hidden rounded-full"
												style="background: var(--color-surface-3);"
											>
												<div
													class="h-full rounded-full transition-all"
													style="width: {pct(quota)}%; background: {pct(quota) >= 90
														? 'var(--color-danger)'
														: pct(quota) >= 70
															? 'var(--color-warning)'
															: 'var(--color-brand)'};"
												></div>
											</div>
											<span
												class="w-8 text-right text-xs text-[var(--color-text-muted)] tabular-nums"
											>
												{pct(quota)}%
											</span>
										</div>
									</td>
									<td data-label={m.redis_col_reqmin()} class="tabular-nums"
										>{quota.requests_this_minute}</td
									>
									<td
										data-label={m.redis_col_reqmin_limit()}
										class="text-[var(--color-text-muted)] tabular-nums"
									>
										{quota.requests_per_minute_limit}
									</td>
									<td data-label="" class="w-24">
										{#if confirmResetUserId === quota.user_id}
											<div class="flex items-center justify-end gap-1">
												<button
													onclick={() => resetQuota(quota.user_id)}
													class="touch-target rounded-lg p-1.5 transition-colors"
													style="color: var(--color-danger);"
													aria-label={m.redis_confirm_reset()}
													title={m.redis_confirm_reset()}
												>
													<Check size={14} />
												</button>
												<button
													onclick={() => (confirmResetUserId = null)}
													class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-3)]"
													aria-label={m.common_cancel()}
													title={m.common_cancel()}
												>
													<X size={14} />
												</button>
											</div>
										{:else}
											<button
												onclick={() => (confirmResetUserId = quota.user_id)}
												class="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-danger)]"
												title={m.redis_reset_quota()}
											>
												<RefreshCw size={12} />
												{m.redis_reset()}
											</button>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>
			<p class="text-xs text-[var(--color-text-muted)]">
				{m.redis_users_with_counters({ n: $quotas.data!.length })}
			</p>
		{/if}
	</section>
</div>
