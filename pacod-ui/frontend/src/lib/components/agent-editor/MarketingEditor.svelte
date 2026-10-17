<script lang="ts">
	/**
	 * Public listing of the agent (graph `meta.marketing`): the landing-page card
	 * and the /agents/<slug> page, per locale. Published with the agent — a
	 * production release with "Show on landing page" ticked appears on the public
	 * site (and in the sitemap) without a frontend deploy.
	 */
	import { m } from '$lib/paraglide/messages';
	import type { AgentGraph, Issue } from '$lib/features/agent-editor/queries';
	import { AGENT_ICONS, AGENT_ICON_NAMES } from '$lib/marketing/agent-icons';

	interface Props {
		graph: AgentGraph;
		issues: Issue[];
		readonly: boolean;
		onmarketing: (marketing: Record<string, unknown> | null) => void;
	}

	let { graph, issues, readonly, onmarketing }: Props = $props();

	const LOCALES = ['en', 'bg', 'de', 'ru'] as const;
	let locale = $state<(typeof LOCALES)[number]>('en');

	type Rec = Record<string, unknown>;
	const mk = $derived(((graph.meta as Rec).marketing ?? null) as Rec | null);
	const card = $derived(((mk?.card ?? {}) as Record<string, Rec>)[locale] ?? {});
	const page = $derived(((mk?.page ?? {}) as Record<string, Rec>)[locale] ?? {});
	const mkIssues = $derived(issues.filter((i) => i.path.startsWith('/meta/marketing')));

	function set(patch: Rec) {
		onmarketing({ ...(mk ?? {}), ...patch });
	}
	function setCard(patch: Rec) {
		const cards = { ...((mk?.card ?? {}) as Rec) };
		cards[locale] = { name: '', desc: '', tag: null, ...card, ...patch };
		set({ card: cards });
	}
	function setPage(patch: Rec) {
		const pages = { ...((mk?.page ?? {}) as Rec) };
		pages[locale] = { ...page, ...patch };
		set({ page: pages });
	}

	// "Title :: body" lines ⇄ [{title, body}] (value props / FAQ).
	function pairsToText(list: unknown, a: string, b: string): string {
		return Array.isArray(list)
			? list.map((x) => `${(x as Rec)[a] ?? ''} :: ${(x as Rec)[b] ?? ''}`).join('\n')
			: '';
	}
	function textToPairs(text: string, a: string, b: string): Rec[] {
		return text
			.split('\n')
			.map((l) => l.trim())
			.filter(Boolean)
			.map((l) => {
				const [x, ...rest] = l.split('::');
				return { [a]: x.trim(), [b]: rest.join('::').trim() };
			});
	}

	const TEXT_FIELDS: { key: string; label: () => string; area?: boolean }[] = [
		{ key: 'eyebrow', label: m.agent_editor_mk_eyebrow },
		{ key: 'title', label: m.agent_editor_mk_title },
		{ key: 'lead', label: m.agent_editor_mk_lead, area: true },
		{ key: 'metaTitle', label: m.agent_editor_mk_meta_title },
		{ key: 'metaDescription', label: m.agent_editor_mk_meta_description, area: true },
		{ key: 'valuePropsTitle', label: m.agent_editor_mk_value_props_title },
		{ key: 'useCasesTitle', label: m.agent_editor_mk_use_cases_title },
		{ key: 'faqTitle', label: m.agent_editor_mk_faq_title },
		{ key: 'ctaTitle', label: m.agent_editor_mk_cta_title },
		{ key: 'ctaBody', label: m.agent_editor_mk_cta_body, area: true },
		{ key: 'ctaLabel', label: m.agent_editor_mk_cta_label },
		{ key: 'homeLabel', label: m.agent_editor_mk_home_label }
	];
</script>

<div class="space-y-3">
	<h2 class="text-sm font-semibold" style="color: var(--color-text);">
		{m.agent_editor_mk_heading()}
	</h2>
	<p class="text-[11px]" style="color: var(--color-text-faint);">{m.agent_editor_mk_intro()}</p>

	{#if !mk}
		<button
			type="button"
			class="rounded-md border px-3 py-1.5 text-xs"
			style="border-color: var(--color-border-strong); color: var(--color-text);"
			disabled={readonly}
			onclick={() =>
				onmarketing({
					slug: '',
					show_on_landing: false,
					order: 100,
					icon: 'chat',
					card: {},
					page: {}
				})}
		>
			{m.agent_editor_mk_enable()}
		</button>
	{:else}
		{#each mkIssues as issue, i (i)}
			<p
				class="rounded px-2 py-1 text-[11px]"
				style="background: var(--color-danger-soft); color: var(--color-danger);"
			>
				{issue.message}
			</p>
		{/each}

		<div class="grid grid-cols-2 gap-2">
			<label class="block">
				<span class="field-label">{m.agent_editor_mk_slug()}</span>
				<input
					class="field-input w-full font-mono text-xs"
					disabled={readonly}
					value={String(mk.slug ?? '')}
					oninput={(e) => set({ slug: e.currentTarget.value.trim().toLowerCase() })}
				/>
			</label>
			<label class="block">
				<span class="field-label">{m.agent_editor_mk_order()}</span>
				<input
					class="field-input w-full text-xs"
					type="number"
					disabled={readonly}
					value={Number(mk.order ?? 100)}
					oninput={(e) => set({ order: Math.round(e.currentTarget.valueAsNumber || 0) })}
				/>
			</label>
			<label class="block">
				<span class="field-label">{m.agent_editor_mk_accent()}</span>
				<input
					class="h-8 w-full rounded border"
					type="color"
					disabled={readonly}
					value={String(mk.accent || '#0e6e57')}
					oninput={(e) => set({ accent: e.currentTarget.value })}
				/>
			</label>
			<label class="block">
				<span class="field-label">{m.agent_editor_mk_soft()}</span>
				<input
					class="h-8 w-full rounded border"
					type="color"
					disabled={readonly}
					value={String(mk.soft || '#e7f1ed')}
					oninput={(e) => set({ soft: e.currentTarget.value })}
				/>
			</label>
		</div>

		<div>
			<span class="field-label">{m.agent_editor_mk_icon()}</span>
			<div class="flex flex-wrap gap-1.5">
				{#each AGENT_ICON_NAMES as name (name)}
					<button
						type="button"
						class="flex h-9 w-9 items-center justify-center rounded-md border"
						style="border-color: {mk.icon === name ? 'var(--color-brand)' : 'var(--color-border)'};
							background: {mk.icon === name ? String(mk.soft || 'var(--color-brand-soft)') : 'transparent'};
							color: {String(mk.accent || 'var(--color-brand)')};"
						title={name}
						disabled={readonly}
						onclick={() => set({ icon: name })}
					>
						<svg
							width="18"
							height="18"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.6"
							stroke-linecap="round"
							stroke-linejoin="round"
						>
							<!-- static markup from AGENT_ICONS (never data-supplied) -->
							<!-- eslint-disable-next-line svelte/no-at-html-tags -->
							{@html AGENT_ICONS[name]}
						</svg>
					</button>
				{/each}
			</div>
		</div>

		<label class="flex items-center gap-2 text-xs" style="color: var(--color-text);">
			<input
				type="checkbox"
				disabled={readonly}
				checked={mk.show_on_landing === true}
				onchange={(e) => set({ show_on_landing: e.currentTarget.checked })}
			/>
			{m.agent_editor_mk_show()}
		</label>

		<div class="flex gap-1 border-b" style="border-color: var(--color-border);">
			{#each LOCALES as loc (loc)}
				<button
					type="button"
					class="-mb-px border-b-2 px-2 py-1 text-xs font-medium uppercase"
					style="border-color: {locale === loc ? 'var(--color-brand)' : 'transparent'};
						color: {locale === loc ? 'var(--color-brand)' : 'var(--color-text-muted)'};"
					onclick={() => (locale = loc)}
				>
					{loc}
				</button>
			{/each}
		</div>

		<fieldset class="space-y-2 rounded-md border p-2" style="border-color: var(--color-border);">
			<legend class="px-1 text-[11px] font-semibold" style="color: var(--color-text-muted);">
				{m.agent_editor_mk_card()}
			</legend>
			<input
				class="field-input w-full text-xs"
				placeholder={m.agent_editor_mk_card_name()}
				disabled={readonly}
				value={String(card.name ?? '')}
				oninput={(e) => setCard({ name: e.currentTarget.value })}
			/>
			<textarea
				class="field-textarea w-full text-xs"
				rows="2"
				placeholder={m.agent_editor_mk_card_desc()}
				disabled={readonly}
				value={String(card.desc ?? '')}
				oninput={(e) => setCard({ desc: e.currentTarget.value })}></textarea>
			<input
				class="field-input w-full text-xs"
				placeholder={m.agent_editor_mk_card_tag()}
				disabled={readonly}
				value={String(card.tag ?? '')}
				oninput={(e) => setCard({ tag: e.currentTarget.value || null })}
			/>
		</fieldset>

		<fieldset class="space-y-2 rounded-md border p-2" style="border-color: var(--color-border);">
			<legend class="px-1 text-[11px] font-semibold" style="color: var(--color-text-muted);">
				{m.agent_editor_mk_page()}
			</legend>
			{#each TEXT_FIELDS as f (f.key)}
				<label class="block">
					<span class="text-[10px]" style="color: var(--color-text-faint);">{f.label()}</span>
					{#if f.area}
						<textarea
							class="field-textarea w-full text-xs"
							rows="2"
							disabled={readonly}
							value={String(page[f.key] ?? '')}
							oninput={(e) => setPage({ [f.key]: e.currentTarget.value })}></textarea>
					{:else}
						<input
							class="field-input w-full text-xs"
							disabled={readonly}
							value={String(page[f.key] ?? '')}
							oninput={(e) => setPage({ [f.key]: e.currentTarget.value })}
						/>
					{/if}
				</label>
			{/each}
			<label class="block">
				<span class="text-[10px]" style="color: var(--color-text-faint);"
					>{m.agent_editor_mk_value_props()}</span
				>
				<textarea
					class="field-textarea w-full text-xs"
					rows="4"
					placeholder={m.agent_editor_mk_pairs_placeholder()}
					disabled={readonly}
					value={pairsToText(page.valueProps, 'title', 'body')}
					onchange={(e) =>
						setPage({ valueProps: textToPairs(e.currentTarget.value, 'title', 'body') })}
				></textarea>
			</label>
			<label class="block">
				<span class="text-[10px]" style="color: var(--color-text-faint);"
					>{m.agent_editor_mk_use_cases()}</span
				>
				<textarea
					class="field-textarea w-full text-xs"
					rows="4"
					placeholder={m.agent_editor_list_placeholder()}
					disabled={readonly}
					value={Array.isArray(page.useCases) ? (page.useCases as string[]).join('\n') : ''}
					onchange={(e) =>
						setPage({
							useCases: e.currentTarget.value
								.split('\n')
								.map((s) => s.trim())
								.filter(Boolean)
						})}></textarea>
			</label>
			<label class="block">
				<span class="text-[10px]" style="color: var(--color-text-faint);"
					>{m.agent_editor_mk_faq()}</span
				>
				<textarea
					class="field-textarea w-full text-xs"
					rows="4"
					placeholder={m.agent_editor_mk_pairs_placeholder()}
					disabled={readonly}
					value={pairsToText(page.faq, 'q', 'a')}
					onchange={(e) => setPage({ faq: textToPairs(e.currentTarget.value, 'q', 'a') })}
				></textarea>
			</label>
		</fieldset>

		{#if !readonly}
			<button
				type="button"
				class="text-[11px] underline"
				style="color: var(--color-danger);"
				onclick={() => onmarketing(null)}
			>
				{m.agent_editor_mk_remove()}
			</button>
		{/if}
	{/if}
</div>
