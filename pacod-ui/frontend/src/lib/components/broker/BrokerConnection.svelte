<script lang="ts">
	/**
	 * How pacod-ap reaches pacod-broker (admin_api/routes/broker.py, /config). Every setting
	 * shows where it comes from: the deployment (BROKER_* in the environment) until it is set
	 * here. The token is write-only — only whether there is one and its last four characters come
	 * back — and it goes only to the address it was given for, so a new address needs its own
	 * token (or none, for a broker in open mode); the server refuses the change otherwise.
	 * Superusers change the connection; admins see it and may test it.
	 */
	import { CircleCheck, CircleX, Plug, RotateCcw } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import { m } from '$lib/paraglide/messages';
	import {
		BROKER_NUMBER_FIELDS,
		BROKER_TEXT_FIELDS,
		brokerErrorMessage,
		createBrokerConfigQuery,
		resetBrokerConfigMutation,
		testBrokerConnection,
		updateBrokerConfigMutation,
		type BrokerConfig,
		type BrokerConfigUpdate,
		type BrokerNumberField,
		type BrokerProbe,
		type BrokerTextField
	} from '$lib/features/broker/queries';

	let { initial, canEdit }: { initial: BrokerConfig; canEdit: boolean } = $props();

	const configQuery = createBrokerConfigQuery();
	const updateMut = updateBrokerConfigMutation();
	const resetMut = resetBrokerConfigMutation();
	const config = $derived($configQuery.data ?? initial);

	type Field = BrokerTextField | BrokerNumberField;

	// The form starts from the settings in force; a save or a reset loads what the server says.
	// svelte-ignore state_referenced_locally
	let text = $state(textOf(initial));
	// svelte-ignore state_referenced_locally
	let numbers = $state(numbersOf(initial));
	let token = $state('');
	let openMode = $state(false);
	let error = $state<string | null>(null);
	let saved = $state(false);
	let probe = $state<BrokerProbe | null>(null);
	let probing = $state(false);
	const busy = $derived($updateMut.isPending || $resetMut.isPending);

	function textOf(c: BrokerConfig): Record<BrokerTextField, string> {
		return { url: c.url.value, agent_url: c.agent_url.value, mcp_url: c.mcp_url.value };
	}
	function numbersOf(c: BrokerConfig): Record<BrokerNumberField, number> {
		return {
			refresh_seconds: c.refresh_seconds.value,
			ttl_seconds: c.ttl_seconds.value,
			search_timeout_seconds: c.search_timeout_seconds.value
		};
	}
	function load(c: BrokerConfig) {
		text = textOf(c);
		numbers = numbersOf(c);
		token = '';
		openMode = false;
	}

	const labels: Record<Field, () => string> = {
		url: m.broker_conn_url,
		agent_url: m.broker_conn_agent_url,
		mcp_url: m.broker_conn_mcp_url,
		refresh_seconds: m.broker_conn_refresh,
		ttl_seconds: m.broker_conn_ttl,
		search_timeout_seconds: m.broker_conn_search_timeout
	};
	const hints: Record<Field, () => string> = {
		url: m.broker_conn_url_hint,
		agent_url: m.broker_conn_agent_url_hint,
		mcp_url: m.broker_conn_mcp_url_hint,
		refresh_seconds: m.broker_conn_refresh_hint,
		ttl_seconds: m.broker_conn_ttl_hint,
		search_timeout_seconds: m.broker_conn_search_timeout_hint
	};
	const placeholders: Record<BrokerTextField, string> = {
		url: 'http://pacod-broker:8060',
		agent_url: 'https://agents.example.com',
		mcp_url: 'https://agents.example.com/mcp'
	};

	// What a save sends: the fields that differ from the settings in force, and the token when
	// one was typed (or open mode chosen).
	const changes = $derived.by(() => {
		const body: BrokerConfigUpdate = {};
		for (const f of BROKER_TEXT_FIELDS) {
			if (text[f].trim() !== config[f].value) body[f] = text[f].trim();
		}
		for (const f of BROKER_NUMBER_FIELDS) {
			const n = Number(numbers[f]);
			if (Number.isFinite(n) && n !== config[f].value) body[f] = n;
		}
		if (openMode) body.token = '';
		else if (token.trim()) body.token = token.trim();
		return body;
	});
	const dirty = $derived(Object.keys(changes).length > 0);
	const urlChanged = $derived(text.url.trim() !== config.url.value);
	const anySetHere = $derived(
		[...BROKER_TEXT_FIELDS, ...BROKER_NUMBER_FIELDS].some((f) => config[f].source === 'console') ||
			config.token.source === 'console'
	);

	function begin() {
		error = null;
		saved = false;
		probe = null;
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		begin();
		try {
			load(await $updateMut.mutateAsync(changes));
			saved = true;
		} catch (e) {
			error = brokerErrorMessage(e);
		}
	}

	/** One setting back to the deployment's value; the form's other edits stay. */
	async function useDeployment(field: Field | 'token') {
		begin();
		try {
			const c = await $updateMut.mutateAsync({ [field]: null });
			if (field === 'token') {
				token = '';
				openMode = false;
			} else if ((BROKER_TEXT_FIELDS as readonly string[]).includes(field)) {
				text[field as BrokerTextField] = c[field as BrokerTextField].value;
			} else {
				numbers[field as BrokerNumberField] = c[field as BrokerNumberField].value;
			}
		} catch (e) {
			error = brokerErrorMessage(e);
		}
	}

	async function resetAll() {
		if (!confirm(m.broker_conn_reset_confirm())) return;
		begin();
		try {
			load(await $resetMut.mutateAsync(undefined));
			saved = true;
		} catch (e) {
			error = brokerErrorMessage(e);
		}
	}

	async function test() {
		begin();
		probing = true;
		try {
			probe = await testBrokerConnection();
		} catch (e) {
			error = brokerErrorMessage(e);
		} finally {
			probing = false;
		}
	}

	function sourceLabel(s: string): string {
		if (s === 'console') return m.broker_conn_source_console();
		if (s === 'deployment') return m.broker_conn_source_deployment();
		return m.broker_conn_source_none();
	}
</script>

{#snippet badge(s: string)}
	<span
		class="rounded px-1.5 py-0.5 text-[10px] font-medium tracking-wide uppercase"
		style="background: var(--color-surface-2); color: var(--color-text-muted);"
		>{sourceLabel(s)}</span
	>
{/snippet}

{#snippet deployment(f: Field)}
	{#if config[f].source === 'console'}
		<div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
			<span class="font-mono"
				>{m.broker_conn_deployment_value({ value: String(config[f].default || '—') })}</span
			>
			{#if canEdit}
				<Button
					size="xs"
					variant="ghost"
					type="button"
					disabled={busy}
					onclick={() => useDeployment(f)}
					><RotateCcw size={12} />{m.broker_conn_use_deployment()}</Button
				>
			{/if}
		</div>
	{/if}
{/snippet}

<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
	<div class="mb-2 flex items-center gap-2">
		<Plug size={16} />
		<h2 class="text-base font-semibold">{m.broker_conn_heading()}</h2>
	</div>
	<p class="mb-1 max-w-3xl text-sm text-[var(--color-text-muted)]">{m.broker_conn_intro()}</p>
	{#if !canEdit}
		<p class="mb-1 text-xs text-[var(--color-text-muted)]">{m.broker_conn_read_only()}</p>
	{/if}

	<form class="mt-4 space-y-5" onsubmit={save}>
		<div class="grid gap-5 md:grid-cols-2">
			{#each BROKER_TEXT_FIELDS as f (f)}
				<div>
					<label class="mb-1 flex items-center gap-2 text-sm font-medium" for="broker-{f}">
						{labels[f]()}
						{@render badge(config[f].source)}
					</label>
					<input
						id="broker-{f}"
						class="field-input w-full font-mono text-xs"
						type="url"
						maxlength="500"
						autocomplete="off"
						spellcheck="false"
						placeholder={placeholders[f]}
						disabled={!canEdit}
						bind:value={text[f]}
					/>
					<p class="mt-1 text-xs text-[var(--color-text-muted)]">{hints[f]()}</p>
					{@render deployment(f)}
				</div>
			{/each}

			<div>
				<label class="mb-1 flex items-center gap-2 text-sm font-medium" for="broker-token">
					{m.broker_conn_token()}
					{@render badge(config.token.source)}
				</label>
				<p class="mb-1 text-xs">
					{#if config.token.configured}
						{config.token.last4
							? m.broker_conn_token_set({ last4: config.token.last4 })
							: m.broker_conn_token_set_short()}
					{:else}
						{m.broker_conn_token_none()}
					{/if}
				</p>
				{#if canEdit && config.token_editable}
					<input
						id="broker-token"
						class="field-input w-full font-mono text-xs"
						type="password"
						maxlength="4000"
						autocomplete="off"
						placeholder={urlChanged
							? m.broker_conn_token_placeholder_new()
							: m.broker_conn_token_placeholder()}
						disabled={openMode}
						bind:value={token}
					/>
				{:else if canEdit}
					<p class="text-xs" style="color: var(--color-warning);">
						{m.broker_conn_token_not_editable()}
					</p>
				{/if}
				{#if canEdit}
					<label class="mt-2 flex items-center gap-2 text-xs">
						<input type="checkbox" bind:checked={openMode} />
						{m.broker_conn_open_mode()}
					</label>
				{/if}
				<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.broker_conn_token_hint()}</p>
				{#if canEdit && config.token.source === 'console'}
					<Button
						size="xs"
						variant="ghost"
						type="button"
						class="mt-1"
						disabled={busy}
						onclick={() => useDeployment('token')}
						><RotateCcw size={12} />{m.broker_conn_token_use_deployment()}</Button
					>
				{/if}
			</div>
		</div>

		<details>
			<summary class="cursor-pointer text-sm font-medium">{m.broker_conn_timings()}</summary>
			<div class="mt-3 grid gap-5 md:grid-cols-3">
				{#each BROKER_NUMBER_FIELDS as f (f)}
					<div>
						<label class="mb-1 flex items-center gap-2 text-sm font-medium" for="broker-{f}">
							{labels[f]()}
							{@render badge(config[f].source)}
						</label>
						<input
							id="broker-{f}"
							class="field-input w-full"
							type="number"
							step={f === 'ttl_seconds' ? 1 : 'any'}
							min={config.bounds[f]?.min}
							max={config.bounds[f]?.max}
							disabled={!canEdit}
							bind:value={numbers[f]}
						/>
						<p class="mt-1 text-xs text-[var(--color-text-muted)]">{hints[f]()}</p>
						{@render deployment(f)}
					</div>
				{/each}
			</div>
		</details>

		<div class="flex flex-wrap items-center gap-2">
			{#if canEdit}
				<Button type="submit" size="sm" disabled={!dirty || busy}>{m.common_save()}</Button>
			{/if}
			<Button
				type="button"
				size="sm"
				variant="secondary"
				disabled={probing || busy || !config.configured}
				onclick={test}>{m.broker_conn_test()}</Button
			>
			{#if canEdit && anySetHere}
				<Button type="button" size="sm" variant="danger-ghost" disabled={busy} onclick={resetAll}
					>{m.broker_conn_reset()}</Button
				>
			{/if}
			{#if saved}
				<span class="text-xs" style="color: var(--color-success);">{m.broker_conn_saved()}</span>
			{/if}
		</div>
	</form>

	{#if error}
		<p class="mt-3 text-sm" style="color: var(--color-danger);">{error}</p>
	{/if}
	{#if probe}
		<div class="mt-3 flex items-start gap-2 text-sm">
			{#if probe.reachable && probe.accepted}
				<CircleCheck size={16} class="mt-0.5 shrink-0" style="color: var(--color-success);" />
				<p>
					{probe.client
						? m.broker_conn_test_ok_as({ client: probe.client })
						: m.broker_conn_test_ok()}
				</p>
			{:else}
				<CircleX size={16} class="mt-0.5 shrink-0" style="color: var(--color-danger);" />
				<p>
					{#if !probe.reachable}
						{m.broker_conn_test_unreachable({ error: probe.error ?? '' })}
					{:else if probe.accepted === false}
						{m.broker_conn_test_refused({ error: probe.error ?? '' })}
					{:else}
						{m.broker_conn_test_failed({ error: probe.error ?? '' })}
					{/if}
				</p>
			{/if}
		</div>
	{/if}
</section>
