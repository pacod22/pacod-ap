<script lang="ts">
	import { Radar, AlertTriangle, Search } from 'lucide-svelte';
	import {
		BROKER_KINDS,
		createBrokerCatalogueQuery,
		createBrokerStatusQuery,
		searchBroker,
		type BrokerCatalogueItem,
		type BrokerHit
	} from '$lib/features/broker/queries';
	import { m } from '$lib/paraglide/messages';
	import Button from '$lib/components/ui/Button.svelte';
	import BrokerConnection from '$lib/components/broker/BrokerConnection.svelte';
	import GatewayConsoleLink from '$lib/components/gateway/GatewayConsoleLink.svelte';

	let { data } = $props();

	// An agent is offered from its published version in the agent editor; the connection to the
	// broker is the panel below (superusers change it).
	const statusQuery = createBrokerStatusQuery();
	const status = $derived($statusQuery.data ?? data.initial);

	// Models and templates the broker lists, each with how the gateway offers it.
	const catalogueQuery = createBrokerCatalogueQuery();
	const catalogue = $derived($catalogueQuery.data);
	let catalogueFilter = $state('');
	let catalogueKind = $state('');
	const catalogueRows = $derived.by(() => {
		const needle = catalogueFilter.trim().toLowerCase();
		return (catalogue?.models ?? []).filter(
			(r: BrokerCatalogueItem) =>
				(!catalogueKind || r.kind === catalogueKind) &&
				(!needle || `${r.name} ${r.upstream_id} ${r.provider}`.toLowerCase().includes(needle))
		);
	});

	let q = $state('');
	let kind = $state('');
	let hits = $state<BrokerHit[] | null>(null);
	let searchError = $state<string | null>(null);
	let searching = $state(false);

	async function runSearch(event: SubmitEvent) {
		event.preventDefault();
		searching = true;
		try {
			const r = await searchBroker(q, kind);
			hits = r.hits;
			searchError = r.broker_error;
		} catch (e) {
			hits = [];
			searchError = String(e);
		} finally {
			searching = false;
		}
	}

	function reputation(h: BrokerHit | null): string {
		if (!h?.reputation) return '—';
		return `${h.reputation.score.toFixed(2)}${h.reputation.label ? ` (${h.reputation.label})` : ''}`;
	}
</script>

<svelte:head><title>{m.broker_title()}</title></svelte:head>

<div class="space-y-8">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">{m.broker_heading()}</h1>
		<p class="mt-1 max-w-3xl text-sm text-[var(--color-text-muted)]">{m.broker_intro()}</p>
	</div>

	{#if !status.configured}
		<div
			class="flex items-start gap-3 rounded-xl border p-4 text-sm"
			style="border-color: var(--color-border); background: var(--color-surface-2);"
		>
			<AlertTriangle size={16} class="mt-0.5 shrink-0" />
			<p>{m.broker_not_configured()}</p>
		</div>
	{:else if status.broker_error || status.announce_error}
		<div
			class="flex items-start gap-3 rounded-xl border p-4 text-sm"
			style="border-color: var(--color-warning, #b45309); background: var(--color-surface-2);"
		>
			<AlertTriangle size={16} class="mt-0.5 shrink-0" />
			<p>{m.broker_unreachable({ error: status.broker_error ?? status.announce_error ?? '' })}</p>
		</div>
	{/if}

	<BrokerConnection initial={data.config} canEdit={data.canEdit} />

	<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
		<div class="mb-2 flex items-center gap-2">
			<Radar size={16} />
			<h2 class="text-base font-semibold">
				{m.broker_offered_heading({ n: status.offered.length })}
			</h2>
		</div>
		<p class="mb-4 text-sm text-[var(--color-text-muted)]">
			{#if status.agent_url}
				{m.broker_offered_at({ url: status.agent_url })}
			{:else}
				{m.broker_offered_no_url()}
			{/if}
		</p>
		<div class="overflow-x-auto rounded-lg" style="border: 1px solid var(--color-border);">
			<table class="w-full text-sm">
				<thead>
					<tr
						class="text-left text-[var(--color-text-muted)]"
						style="background: var(--color-surface-2);"
					>
						<th class="px-3 py-2 font-medium">{m.broker_col_agent()}</th>
						<th class="px-3 py-2 font-medium">{m.broker_col_offer()}</th>
						<th class="px-3 py-2 font-medium">{m.broker_col_announced()}</th>
					</tr>
				</thead>
				<tbody>
					{#each status.offered as a (a.slug)}
						<tr style="border-top: 1px solid var(--color-border);">
							<td class="px-3 py-2">
								<div class="font-medium">{a.name}</div>
								<div class="text-xs text-[var(--color-text-muted)]">{a.summary}</div>
							</td>
							<td class="px-3 py-2 text-xs">{a.offer}</td>
							<td class="px-3 py-2 font-mono text-xs"
								>{status.announced?.[`agent.${a.slug}`] ?? '—'}</td
							>
						</tr>
					{/each}
					{#if status.offered.length === 0}
						<tr>
							<td colspan="3" class="px-3 py-6 text-center text-sm text-[var(--color-text-muted)]"
								>{m.broker_offered_none()}</td
							>
						</tr>
					{/if}
				</tbody>
			</table>
		</div>
	</section>

	{#if status.configured}
		<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
			<h2 class="mb-2 text-base font-semibold">{m.broker_catalogue_heading()}</h2>
			<p class="mb-4 max-w-3xl text-sm text-[var(--color-text-muted)]">
				{m.broker_catalogue_intro()}
			</p>
			<div class="mb-4 flex flex-wrap items-center gap-2">
				<input
					class="field-input min-w-64 flex-1"
					bind:value={catalogueFilter}
					placeholder={m.broker_catalogue_filter()}
					aria-label={m.broker_catalogue_filter()}
				/>
				<select class="field-input" bind:value={catalogueKind} aria-label={m.broker_col_kind()}>
					<option value="">{m.broker_kind_any()}</option>
					<option value="model">model</option>
					<option value="embedding">embedding</option>
				</select>
				<GatewayConsoleLink href={catalogue?.console_url} label={m.broker_catalogue_discovery()} />
			</div>
			{#if catalogue?.broker_error}
				<p class="mb-3 text-sm" style="color: var(--color-danger);">
					{m.broker_unreachable({ error: catalogue.broker_error })}
				</p>
			{/if}
			<div class="overflow-x-auto rounded-lg" style="border: 1px solid var(--color-border);">
				<table class="w-full text-sm">
					<thead>
						<tr
							class="text-left text-[var(--color-text-muted)]"
							style="background: var(--color-surface-2);"
						>
							<th class="px-3 py-2 font-medium">{m.broker_col_resource()}</th>
							<th class="px-3 py-2 font-medium">{m.broker_col_provider()}</th>
							<th class="px-3 py-2 font-medium">{m.broker_col_price()}</th>
							<th class="px-3 py-2 font-medium">{m.broker_col_reputation()}</th>
							<th class="px-3 py-2 font-medium">{m.broker_col_status()}</th>
						</tr>
					</thead>
					<tbody>
						{#each catalogueRows as r (r.id)}
							<tr style="border-top: 1px solid var(--color-border);">
								<td class="px-3 py-2">
									<div class="font-mono text-xs">{r.name}</div>
									<div class="text-xs text-[var(--color-text-muted)]">
										{r.kind}{r.gateway ? ' · ' + m.broker_catalogue_template() : ''}
									</div>
								</td>
								<td class="px-3 py-2 text-xs">{r.provider || '—'}</td>
								<td class="px-3 py-2 text-xs">{r.price || '—'}</td>
								<td class="px-3 py-2 text-xs">{reputation(r)}</td>
								<td class="px-3 py-2 text-xs">
									{#if r.usage.state === 'usable'}
										<span style="color: var(--color-success);"
											>{m.broker_catalogue_usable({ call: r.usage.call ?? '' })}</span
										>
									{:else}
										<span class="text-[var(--color-text-muted)]"
											>{m.broker_catalogue_not_in_gateway()}</span
										>
									{/if}
								</td>
							</tr>
						{/each}
						{#if catalogueRows.length === 0}
							<tr>
								<td colspan="5" class="px-3 py-6 text-center text-sm text-[var(--color-text-muted)]"
									>{catalogue ? m.broker_catalogue_none() : m.common_loading()}</td
								>
							</tr>
						{/if}
					</tbody>
				</table>
			</div>
		</section>
	{/if}

	<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
		<h2 class="mb-2 text-base font-semibold">{m.broker_models_heading()}</h2>
		<p class="mb-4 text-sm text-[var(--color-text-muted)]">{m.broker_models_intro()}</p>
		<div class="overflow-x-auto rounded-lg" style="border: 1px solid var(--color-border);">
			<table class="w-full text-sm">
				<thead>
					<tr
						class="text-left text-[var(--color-text-muted)]"
						style="background: var(--color-surface-2);"
					>
						<th class="px-3 py-2 font-medium">{m.llm_models_col_model()}</th>
						<th class="px-3 py-2 font-medium">{m.broker_col_reputation()}</th>
						<th class="px-3 py-2 font-medium">{m.broker_col_price()}</th>
						<th class="px-3 py-2 font-medium">{m.broker_col_status()}</th>
					</tr>
				</thead>
				<tbody>
					{#each status.models as row (row.id)}
						<tr style="border-top: 1px solid var(--color-border);">
							<td class="px-3 py-2 font-mono text-xs">{row.id}</td>
							<td class="px-3 py-2 text-xs">{reputation(row.facts)}</td>
							<td class="px-3 py-2 text-xs">{row.facts?.price || '—'}</td>
							<td class="px-3 py-2 text-xs">{row.facts?.status || '—'}</td>
						</tr>
					{/each}
					{#if status.models.length === 0}
						<tr>
							<td colspan="4" class="px-3 py-6 text-center text-sm text-[var(--color-text-muted)]"
								>{m.broker_models_none()}</td
							>
						</tr>
					{/if}
				</tbody>
			</table>
		</div>
	</section>

	{#if status.configured}
		<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
			<h2 class="mb-4 text-base font-semibold">{m.broker_search_heading()}</h2>
			<form class="flex flex-wrap items-center gap-2" onsubmit={runSearch}>
				<label class="field-input flex min-w-64 flex-1 items-center gap-2">
					<Search size={14} />
					<input
						class="flex-1 bg-transparent outline-none"
						bind:value={q}
						maxlength="200"
						placeholder={m.broker_search_placeholder()}
						aria-label={m.broker_search_heading()}
					/>
				</label>
				<select class="field-input" bind:value={kind} aria-label={m.broker_col_kind()}>
					<option value="">{m.broker_kind_any()}</option>
					{#each BROKER_KINDS as k (k)}
						<option value={k}>{k}</option>
					{/each}
				</select>
				<Button type="submit" size="sm" disabled={searching}>{m.broker_search_button()}</Button>
			</form>
			{#if searchError}
				<p class="mt-3 text-sm text-[var(--color-danger,#b91c1c)]">
					{m.broker_unreachable({ error: searchError })}
				</p>
			{/if}
			{#if hits}
				<div class="mt-4 overflow-x-auto rounded-lg" style="border: 1px solid var(--color-border);">
					<table class="w-full text-sm">
						<thead>
							<tr
								class="text-left text-[var(--color-text-muted)]"
								style="background: var(--color-surface-2);"
							>
								<th class="px-3 py-2 font-medium">{m.broker_col_resource()}</th>
								<th class="px-3 py-2 font-medium">{m.broker_col_kind()}</th>
								<th class="px-3 py-2 font-medium">{m.broker_col_provider()}</th>
								<th class="px-3 py-2 font-medium">{m.broker_col_price()}</th>
								<th class="px-3 py-2 font-medium">{m.broker_col_reputation()}</th>
							</tr>
						</thead>
						<tbody>
							{#each hits as h (h.id)}
								<tr style="border-top: 1px solid var(--color-border);">
									<td class="px-3 py-2">
										<div class="font-medium">{h.name}</div>
										<div class="text-xs text-[var(--color-text-muted)]">{h.summary}</div>
										<!-- Addresses other providers wrote: shown, never linked. -->
										{#each h.interfaces as i (i.protocol + i.url)}
											<div class="font-mono text-[11px] text-[var(--color-text-muted)]">
												{i.protocol} · {i.url}
											</div>
										{/each}
									</td>
									<td class="px-3 py-2 text-xs">{h.kind}</td>
									<td class="px-3 py-2 text-xs">{h.provider || '—'}</td>
									<td class="px-3 py-2 text-xs">{h.price || '—'}</td>
									<td class="px-3 py-2 text-xs">{reputation(h)}</td>
								</tr>
							{/each}
							{#if hits.length === 0}
								<tr>
									<td
										colspan="5"
										class="px-3 py-6 text-center text-sm text-[var(--color-text-muted)]"
										>{m.broker_search_none()}</td
									>
								</tr>
							{/if}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	{/if}
</div>
