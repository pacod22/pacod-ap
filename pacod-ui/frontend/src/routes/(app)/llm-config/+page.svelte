<script lang="ts">
	import ManagedNotice from '$lib/components/agent-editor/ManagedNotice.svelte';
	import AssistantSettingsForm from '$lib/components/agent-editor/AssistantSettingsForm.svelte';
	import { untrack } from 'svelte';
	import { Cpu, ExternalLink, Globe, KeyRound, Layers, Save, Waypoints } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import UsageTab from '$lib/features/llm-config/UsageTab.svelte';
	import TemplatesTab from '$lib/components/llm-templates/TemplatesTab.svelte';
	import TemplateSelect from '$lib/components/llm-templates/TemplateSelect.svelte';
	import { roleLabel } from '$lib/components/llm-templates/labels';
	import { createTemplatesQuery } from '$lib/features/llm-config/templates';
	import {
		AGENT_ROLES,
		type AgentRole,
		createLLMConfigQuery,
		updateLLMConfigMutation,
		createProviderKeysQuery,
		setProviderKeyMutation,
		deleteProviderKeyMutation,
		type LLMConfigPatch,
		type LLMConfigValues
	} from '$lib/features/llm-config/queries';
	import { createLlmModelsQuery } from '$lib/features/llm-models/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { m } from '$lib/paraglide/messages';

	let { data } = $props();

	let activeTab = $state<'config' | 'templates' | 'usage'>('config');

	// LLM templates are global; every role below picks one (or stays on Legacy).
	const templatesQuery = createTemplatesQuery();
	const templates = $derived($templatesQuery.data?.templates ?? []);

	// The topbar scope selector chooses which profile's LLM config to edit
	// (null → the default profile).
	const cfgQuery = $derived(createLLMConfigQuery(selectedProfile.id));
	const updateMut = $derived(updateLLMConfigMutation(selectedProfile.id));
	const keysQuery = createProviderKeysQuery();
	const setKeyMut = setProviderKeyMutation();
	const deleteKeyMut = deleteProviderKeyMutation();
	// LLM provider credentials live in the gateway console; its URL rides on the
	// (read-only) gateway model list. Client-side, so a gateway outage never
	// blocks this page.
	const gatewayQuery = createLlmModelsQuery();
	const consoleUrl = $derived($gatewayQuery.data?.console_url ?? null);

	// Effective values from the live query, falling back to the SSR snapshot.
	const cfg = $derived($cfgQuery.data ?? data.initial);
	const models = $derived(cfg.available_models);

	// Editable form. Seeded from the SSR snapshot for the first paint, then
	// re-seeded from the profile-scoped query: the SSR load is unscoped (default
	// profile), while Save writes to the topbar-selected profile — without the
	// re-seed the form would show and "restore" the default profile's values.
	const eff = untrack(() => data.initial.effective); // seed once; re-seeded from the scoped query below
	let primary_model = $state(eff.primary_model);
	let classifier_model = $state(eff.classifier_model);
	let planner_model = $state(eff.planner_model);
	let suggestions_model = $state(eff.suggestions_model);
	let newsletter_model = $state(eff.newsletter_model);
	let temperature = $state(eff.temperature);
	let temperature_enabled = $state(eff.temperature_enabled);
	let max_output_tokens = $state(eff.max_output_tokens);
	let planner_max_tokens = $state(eff.planner_max_tokens);
	let classifier_max_tokens = $state(eff.classifier_max_tokens);
	let suggestions_max_tokens = $state(eff.suggestions_max_tokens);
	let thinking_budget_tokens = $state(eff.thinking_budget_tokens);
	let prompt_cache_enabled = $state(eff.prompt_cache_enabled);
	let verification_enabled = $state(eff.verification_enabled);
	let verification_complex_only = $state(eff.verification_complex_only);
	let web_search_enabled = $state(eff.web_search_enabled);
	let web_search_mode = $state(eff.web_search_mode);
	let web_search_native_require_allowlist = $state(eff.web_search_native_require_allowlist);
	let web_search_provider = $state(eff.web_search_provider);
	let allowed_domains_text = $state(eff.web_search_allowed_domains.join('\n'));
	const roleTemplatesOf = (v: LLMConfigValues) =>
		Object.fromEntries(AGENT_ROLES.map((r) => [r, v[`${r}_template`]])) as Record<
			AgentRole,
			string
		>;
	let role_templates = $state(roleTemplatesOf(eff));

	let saveError = $state('');
	let saved = $state(false);

	function seedForm(v: LLMConfigValues) {
		primary_model = v.primary_model;
		classifier_model = v.classifier_model;
		planner_model = v.planner_model;
		suggestions_model = v.suggestions_model;
		newsletter_model = v.newsletter_model;
		temperature = v.temperature;
		temperature_enabled = v.temperature_enabled;
		max_output_tokens = v.max_output_tokens;
		planner_max_tokens = v.planner_max_tokens;
		classifier_max_tokens = v.classifier_max_tokens;
		suggestions_max_tokens = v.suggestions_max_tokens;
		thinking_budget_tokens = v.thinking_budget_tokens;
		prompt_cache_enabled = v.prompt_cache_enabled;
		verification_enabled = v.verification_enabled;
		verification_complex_only = v.verification_complex_only;
		web_search_enabled = v.web_search_enabled;
		web_search_mode = v.web_search_mode;
		web_search_native_require_allowlist = v.web_search_native_require_allowlist;
		web_search_provider = v.web_search_provider;
		allowed_domains_text = v.web_search_allowed_domains.join('\n');
		role_templates = roleTemplatesOf(v);
	}

	// Which profile the form currently holds values for. The SSR snapshot is
	// the unscoped (default) profile, so it is only valid for `null`; any other
	// selection re-seeds as soon as that profile's config arrives.
	let seededFor = $state<string | null | undefined>(null);
	$effect(() => {
		const pid = selectedProfile.id;
		const loaded = $cfgQuery.data;
		if (seededFor === pid) return;
		if (!loaded) {
			// Profile switched and its config is still loading — drop the stale edit.
			if (seededFor !== undefined) seededFor = undefined;
			return;
		}
		seedForm(loaded.effective);
		seededFor = pid;
		saved = false;
		saveError = '';
	});

	// A model that is set but not in the registry must still be selectable.
	function options(current: string): string[] {
		return current && !models.includes(current) ? [current, ...models] : models;
	}

	async function saveConfig() {
		saveError = '';
		saved = false;
		const patch: LLMConfigPatch = {
			primary_model,
			classifier_model,
			planner_model,
			suggestions_model,
			newsletter_model,
			temperature: Number(temperature),
			temperature_enabled,
			max_output_tokens: Number(max_output_tokens),
			planner_max_tokens: Number(planner_max_tokens),
			classifier_max_tokens: Number(classifier_max_tokens),
			suggestions_max_tokens: Number(suggestions_max_tokens),
			thinking_budget_tokens: Number(thinking_budget_tokens),
			prompt_cache_enabled,
			verification_enabled,
			verification_complex_only,
			web_search_enabled,
			web_search_mode,
			web_search_native_require_allowlist,
			web_search_provider,
			web_search_allowed_domains: allowed_domains_text
				.split(/[\n,]/)
				.map((d) => d.trim())
				.filter(Boolean),
			...Object.fromEntries(AGENT_ROLES.map((r) => [`${r}_template`, role_templates[r]]))
		};
		try {
			await $updateMut.mutateAsync(patch);
			saved = true;
		} catch (e: unknown) {
			saveError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	// ----- API keys -----
	let keyInputs = $state<Record<string, string>>({});
	let keyError = $state('');

	async function saveKey(name: string) {
		keyError = '';
		const value = (keyInputs[name] ?? '').trim();
		if (!value) return;
		try {
			await $setKeyMut.mutateAsync({ name, value });
			keyInputs[name] = '';
		} catch (e: unknown) {
			keyError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	async function clearKey(name: string) {
		keyError = '';
		try {
			await $deleteKeyMut.mutateAsync(name);
		} catch (e: unknown) {
			keyError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}
</script>

<svelte:head><title>{m.llm_config_title()}</title></svelte:head>

<div class="space-y-8">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">{m.llm_config_heading()}</h1>
		<p class="mt-1 max-w-2xl text-sm text-[var(--color-text-muted)]">
			{m.llm_config_intro()}
		</p>
	</div>

	<ManagedNotice fields={cfg.managed_fields} />

	<!-- Tabs -->
	<div class="flex gap-1 border-b" style="border-color: var(--color-border);">
		<button
			type="button"
			onclick={() => (activeTab = 'config')}
			class="-mb-px border-b-2 px-3 py-2 text-sm"
			style={activeTab === 'config'
				? 'border-color: var(--color-brand); color: var(--color-text);'
				: 'border-color: transparent; color: var(--color-text-muted);'}
		>
			{m.common_configuration()}
		</button>
		<button
			type="button"
			onclick={() => (activeTab = 'templates')}
			class="-mb-px border-b-2 px-3 py-2 text-sm"
			style={activeTab === 'templates'
				? 'border-color: var(--color-brand); color: var(--color-text);'
				: 'border-color: transparent; color: var(--color-text-muted);'}
		>
			{m.llmtpl_tab()}
		</button>
		<button
			type="button"
			onclick={() => (activeTab = 'usage')}
			class="-mb-px border-b-2 px-3 py-2 text-sm"
			style={activeTab === 'usage'
				? 'border-color: var(--color-brand); color: var(--color-text);'
				: 'border-color: transparent; color: var(--color-text-muted);'}
		>
			{m.llm_config_tab_usage()}
		</button>
	</div>

	{#if activeTab === 'usage'}
		<UsageTab />
	{:else if activeTab === 'templates'}
		<TemplatesTab />
	{:else}
		<!-- LLM template per role -->
		<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
			<div class="mb-1 flex flex-wrap items-center justify-between gap-2">
				<div class="flex items-center gap-2">
					<Layers size={16} />
					<h2 class="text-base font-semibold">{m.llmtpl_roles_heading()}</h2>
				</div>
				<Button size="xs" variant="ghost" onclick={() => (activeTab = 'templates')}
					>{m.llmtpl_manage()}</Button
				>
			</div>
			<p class="mb-4 text-xs text-[var(--color-text-muted)]">{m.llmtpl_roles_hint()}</p>
			<div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
				{#each AGENT_ROLES as role (role)}
					<TemplateSelect
						id={`role-tpl-${role}`}
						label={roleLabel(role)}
						bind:value={role_templates[role]}
						{templates}
						params={cfg.role_params[role] ?? null}
						managed={cfg.managed_roles.includes(role)}
					/>
				{/each}
			</div>
			<div class="mt-4 flex flex-wrap gap-6">
				<label class="flex items-center gap-2 text-sm">
					<input type="checkbox" bind:checked={prompt_cache_enabled} />
					{m.llm_config_prompt_caching()}
				</label>
				<div>
					<label class="flex items-center gap-2 text-sm">
						<input type="checkbox" bind:checked={verification_enabled} />
						{m.llm_config_verification()}
					</label>
					{#if verification_enabled}
						<label class="mt-1 flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
							<input type="checkbox" bind:checked={verification_complex_only} />
							{m.llm_config_verification_complex_only()}
						</label>
					{/if}
				</div>
			</div>
		</section>

		<!-- Legacy per-role models + params (only for roles left on Legacy) -->
		<details class="rounded-xl border p-5" style="border-color: var(--color-border);">
			<summary class="flex cursor-pointer items-center gap-2">
				<Cpu size={16} />
				<span class="text-base font-semibold">{m.llmtpl_legacy_settings()}</span>
			</summary>
			<p class="mt-2 mb-4 text-xs text-[var(--color-text-muted)]">{m.llm_config_models_gen()}</p>

			<div class="grid grid-cols-1 gap-4 md:grid-cols-3">
				<div>
					<label class="field-label" for="fld-page-llm-config-1">{m.llm_config_primary()}</label>
					<select id="fld-page-llm-config-1" bind:value={primary_model} class="field-input">
						{#each options(primary_model) as mdl}<option value={mdl}>{mdl}</option>{/each}
					</select>
				</div>
				<div>
					<label class="field-label" for="fld-page-llm-config-2">{m.llm_config_classifier()}</label>
					<select id="fld-page-llm-config-2" bind:value={classifier_model} class="field-input">
						{#each options(classifier_model) as mdl}<option value={mdl}>{mdl}</option>{/each}
					</select>
				</div>
				<div>
					<label class="field-label" for="fld-page-llm-config-3">{m.llm_config_planner()}</label>
					<select id="fld-page-llm-config-3" bind:value={planner_model} class="field-input">
						<option value="">{m.llm_config_use_primary()}</option>
						{#each options(planner_model).filter(Boolean) as mdl}<option value={mdl}>{mdl}</option
							>{/each}
					</select>
				</div>
				<div>
					<label class="field-label" for="fld-page-llm-config-4">{m.llm_config_suggestions()}</label
					>
					<select id="fld-page-llm-config-4" bind:value={suggestions_model} class="field-input">
						<option value="">{m.llm_config_use_classifier()}</option>
						{#each options(suggestions_model).filter(Boolean) as mdl}<option value={mdl}
								>{mdl}</option
							>{/each}
					</select>
				</div>
				<div>
					<label class="field-label" for="fld-page-llm-config-5">{m.llm_config_newsletter()}</label>
					<select id="fld-page-llm-config-5" bind:value={newsletter_model} class="field-input">
						<option value="">{m.llm_config_use_primary()}</option>
						{#each options(newsletter_model).filter(Boolean) as mdl}<option value={mdl}
								>{mdl}</option
							>{/each}
					</select>
				</div>
			</div>

			<div class="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
				<div>
					<label class="field-label" for="fld-page-llm-config-6">{m.llm_config_max_output()}</label>
					<input
						id="fld-page-llm-config-6"
						type="number"
						min="1"
						bind:value={max_output_tokens}
						class="field-input"
					/>
				</div>
				<div>
					<label class="field-label" for="fld-page-llm-config-7"
						>{m.llm_config_thinking_budget()}</label
					>
					<input
						id="fld-page-llm-config-7"
						type="number"
						min="0"
						bind:value={thinking_budget_tokens}
						class="field-input"
					/>
				</div>
			</div>
			<div class="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
				<div>
					<label class="field-label" for="fld-page-llm-config-8">{m.llm_config_temperature()}</label
					>
					<input
						id="fld-page-llm-config-8"
						type="number"
						step="0.1"
						min="0"
						max="2"
						bind:value={temperature}
						disabled={!temperature_enabled}
						class="field-input disabled:opacity-50"
					/>
					<label class="mt-1 flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
						<input type="checkbox" bind:checked={temperature_enabled} />
						{m.llm_config_send_temperature()}
					</label>
				</div>
				<div>
					<label class="field-label" for="fld-page-llm-config-9">{m.llm_config_planner_max()}</label
					>
					<input
						id="fld-page-llm-config-9"
						type="number"
						min="1"
						bind:value={planner_max_tokens}
						class="field-input"
					/>
				</div>
				<div>
					<label class="field-label" for="fld-page-llm-config-10"
						>{m.llm_config_classifier_max()}</label
					>
					<input
						id="fld-page-llm-config-10"
						type="number"
						min="1"
						bind:value={classifier_max_tokens}
						class="field-input"
					/>
				</div>
				<div>
					<label class="field-label" for="fld-page-llm-config-11"
						>{m.llm_config_suggestions_max()}</label
					>
					<input
						id="fld-page-llm-config-11"
						type="number"
						min="1"
						bind:value={suggestions_max_tokens}
						class="field-input"
					/>
				</div>
			</div>
		</details>

		<!-- Web search -->
		<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
			<div class="mb-4 flex items-center gap-2">
				<Globe size={16} />
				<h2 class="text-base font-semibold">{m.llm_config_web_search()}</h2>
			</div>
			<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
				<div class="space-y-3">
					<label class="flex items-center gap-2 text-sm">
						<input type="checkbox" bind:checked={web_search_enabled} />
						{m.llm_config_enable_web()}
					</label>
					<div>
						<label class="field-label" for="fld-page-llm-config-12">{m.llm_config_backend()}</label>
						<select id="fld-page-llm-config-12" bind:value={web_search_mode} class="field-input">
							<option value="native">{m.llm_config_backend_native()}</option>
							<option value="external">{m.llm_config_backend_external()}</option>
						</select>
						<p class="mt-1 text-xs text-[var(--color-text-muted)]">
							{m.llm_config_backend_hint()}
						</p>
					</div>
					<label class="flex items-center gap-2 text-sm">
						<input
							type="checkbox"
							bind:checked={web_search_native_require_allowlist}
							disabled={web_search_mode !== 'native'}
						/>
						{m.llm_config_require_allowlist()}
					</label>
					<p class="text-xs text-[var(--color-text-muted)]">
						{m.llm_config_require_allowlist_hint()}
					</p>
					<div>
						<label class="field-label" for="fld-page-llm-config-13"
							>{m.llm_config_external_provider()}</label
						>
						<select
							id="fld-page-llm-config-13"
							bind:value={web_search_provider}
							class="field-input"
						>
							<option value="auto">auto</option>
							<option value="tavily">tavily</option>
							<option value="google">google</option>
						</select>
					</div>
				</div>
				<div>
					<label class="field-label" for="fld-page-llm-config-14"
						>{m.llm_config_allowed_domains()}</label
					>
					<textarea
						id="fld-page-llm-config-14"
						bind:value={allowed_domains_text}
						rows={5}
						class="field-textarea"></textarea>
					<p class="mt-1 text-xs text-[var(--color-text-muted)]">
						{m.llm_config_allowed_domains_hint()}
					</p>
				</div>
			</div>
		</section>

		<div class="flex items-center gap-3">
			<Button size="sm" onclick={saveConfig} disabled={$updateMut.isPending}>
				<Save size={14} />
				{m.common_save_configuration()}
			</Button>
			{#if saved}<span class="text-sm" style="color: var(--color-brand);">{m.common_saved()}</span
				>{/if}
			{#if saveError}<span class="text-sm" style="color: var(--color-danger);">{saveError}</span
				>{/if}
		</div>

		<!-- Agent editor AI assistant — superusers only; GLOBAL, not per profile -->
		{#if data.canManageAssistant}
			<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
				<div class="mb-1 flex items-center gap-2">
					<Waypoints size={16} />
					<h2 class="text-base font-semibold">{m.llm_config_editor_assistant()}</h2>
					<span
						class="rounded-full px-2 py-0.5 text-[10px] font-semibold"
						style="background: var(--color-warning-soft); color: var(--color-warning);"
						>{m.llm_config_superuser_only()}</span
					>
				</div>
				<p class="mb-4 text-xs text-[var(--color-text-muted)]">
					{m.llm_config_editor_assistant_hint()}
				</p>
				<AssistantSettingsForm />
			</section>
		{/if}

		<!-- API keys -->
		<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
			<div class="mb-1 flex items-center gap-2">
				<KeyRound size={16} />
				<h2 class="text-base font-semibold">{m.llm_config_provider_keys()}</h2>
			</div>
			{#if $keysQuery.data && !$keysQuery.data.editable}
				<p class="mb-3 text-sm" style="color: var(--color-danger);">
					{m.llm_config_editing_disabled()}
				</p>
			{:else}
				<p class="mb-3 text-sm text-[var(--color-text-muted)]">
					{m.llm_config_keys_hint()}
				</p>
			{/if}
			<p class="mb-4 text-xs text-[var(--color-text-muted)]">
				{m.llm_config_llm_keys_in_gateway()}
				{#if consoleUrl}
					<!-- New tab, never an iframe: the gateway console forbids framing. -->
					<a
						href={consoleUrl}
						target="_blank"
						rel="noopener"
						class="ml-1 inline-flex items-center gap-1 font-medium text-[var(--color-brand)] hover:underline"
					>
						{m.llm_gateway_console_open()}
						<ExternalLink size={11} />
					</a>
				{/if}
			</p>

			{#if $keysQuery.isPending}
				<p class="text-sm text-[var(--color-text-muted)]">{m.common_loading()}</p>
			{:else if $keysQuery.isError}
				<p class="text-sm" style="color: var(--color-danger);">{m.llm_config_load_keys_failed()}</p>
			{:else if $keysQuery.data}
				<div class="space-y-3">
					{#each $keysQuery.data.keys as k (k.name)}
						<div class="flex flex-wrap items-center gap-3">
							<div class="w-56 font-mono text-xs">{k.name}</div>
							<div class="w-28 text-xs text-[var(--color-text-muted)]">
								{#if k.configured}{m.llm_config_key_set({ last4: k.last4 ?? '' })}{:else}<span
										style="color: var(--color-danger);">{m.llm_config_key_unset()}</span
									>{/if}
							</div>
							{#if $keysQuery.data.editable}
								<input
									type="password"
									placeholder={m.llm_config_key_new_value()}
									bind:value={keyInputs[k.name]}
									class="field-input max-w-xs flex-1"
									autocomplete="off"
								/>
								<Button size="sm" onclick={() => saveKey(k.name)} disabled={$setKeyMut.isPending}
									>{m.common_save()}</Button
								>
								{#if k.configured}
									<Button size="sm" variant="ghost" onclick={() => clearKey(k.name)}
										>{m.common_clear()}</Button
									>
								{/if}
							{/if}
						</div>
					{/each}
				</div>
				{#if keyError}<p class="mt-2 text-sm" style="color: var(--color-danger);">
						{keyError}
					</p>{/if}
			{/if}
		</section>
	{/if}
</div>
