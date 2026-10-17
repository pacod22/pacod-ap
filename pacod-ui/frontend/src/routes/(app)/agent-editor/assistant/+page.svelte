<script lang="ts">
	/**
	 * AI assistant settings — the LLM the agent editor's per-component assistant
	 * uses to draft prompts and content. One global config (not per agent); the
	 * same form is also on LLM & Search (superusers only).
	 */
	import { ArrowLeft, Sparkles } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';
	import AssistantSettingsForm from '$lib/components/agent-editor/AssistantSettingsForm.svelte';

	let { data } = $props();
</script>

<div class="mx-auto max-w-3xl">
	<div class="mb-1 flex items-center gap-2">
		<a
			href="/agent-editor"
			class="rounded p-1 hover:bg-[var(--color-surface-3)]"
			title={m.agent_editor_back()}
		>
			<ArrowLeft size={16} />
		</a>
		<Sparkles size={20} style="color: var(--color-brand);" />
		<h1 class="text-lg font-semibold" style="color: var(--color-text);">
			{m.agent_editor_ai_settings_title()}
		</h1>
	</div>
	<p class="mb-4 text-xs" style="color: var(--color-text-muted);">
		{m.agent_editor_ai_settings_intro()}
	</p>

	<div
		class="rounded-lg border p-5"
		style="border-color: var(--color-border); background: var(--color-surface-2);"
	>
		<AssistantSettingsForm initial={data.initial} />
	</div>
</div>
