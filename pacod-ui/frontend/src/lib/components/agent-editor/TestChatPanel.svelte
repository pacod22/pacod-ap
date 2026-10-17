<script lang="ts">
	/**
	 * Chat with the agent's DEV deployment (agent_variant='dev') — the frozen
	 * snapshot made by "Deploy to dev", never the unsaved draft. Plain-text
	 * rendering on purpose: this is a test console, not the product chat.
	 */
	import { Send, RefreshCw, Square } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		isAbortError,
		resolveChatError,
		streamChat,
		type ConversationMessage
	} from '$lib/features/chat';

	interface Props {
		profileId: string;
		hasDev: boolean;
	}

	let { profileId, hasDev }: Props = $props();

	interface Turn {
		question: string;
		answer: string;
		tools: string[];
		error: string | null;
		pending: boolean;
	}

	let turns = $state<Turn[]>([]);
	let input = $state('');
	let sessionId = $state(newSession());
	let controller: AbortController | null = null;
	const busy = $derived(turns.some((t) => t.pending));

	function newSession(): string {
		return `agent-editor-test-${crypto.randomUUID()}`;
	}

	function reset() {
		controller?.abort();
		turns = [];
		sessionId = newSession();
	}

	async function send() {
		const question = input.trim();
		if (!question || busy) return;
		input = '';
		const history: ConversationMessage[] = turns
			.filter((t) => !t.error && t.answer)
			.flatMap((t) => [
				{ role: 'user' as const, content: t.question },
				{ role: 'assistant' as const, content: t.answer }
			]);
		turns.push({ question, answer: '', tools: [], error: null, pending: true });
		const turn = turns[turns.length - 1];
		controller = new AbortController();
		try {
			await streamChat(
				{
					message: question,
					session_id: sessionId,
					history,
					profile_id: profileId,
					agent_variant: 'dev'
				},
				(ev) => {
					if (ev.type === 'token') turn.answer += ev.text;
					else if (ev.type === 'tool_call') turn.tools.push(ev.name);
					else if (ev.type === 'error') turn.error = ev.message;
				},
				controller.signal
			);
		} catch (e) {
			if (!isAbortError(e)) turn.error = resolveChatError(e).message;
		} finally {
			turn.pending = false;
			controller = null;
		}
	}
</script>

<div class="flex h-full flex-col">
	<div class="mb-2 flex items-center gap-2">
		<h2 class="flex-1 text-sm font-semibold" style="color: var(--color-text);">
			{m.agent_editor_test_title()}
		</h2>
		<Button variant="ghost" size="xs" onclick={reset} title={m.agent_editor_test_new()}>
			<RefreshCw size={12} />
		</Button>
	</div>

	{#if !hasDev}
		<p
			class="rounded-md p-3 text-xs"
			style="background: var(--color-surface-3); color: var(--color-text-muted);"
		>
			{m.agent_editor_test_needs_dev()}
		</p>
	{:else}
		<p class="mb-2 text-[11px]" style="color: var(--color-text-faint);">
			{m.agent_editor_test_hint()}
		</p>
		<div class="flex-1 space-y-3 overflow-y-auto pr-1">
			{#each turns as t, i (i)}
				<div class="space-y-1 text-xs">
					<div
						class="rounded-md px-2 py-1.5"
						style="background: var(--color-brand-soft); color: var(--color-text);"
					>
						{t.question}
					</div>
					{#if t.tools.length}
						<div class="text-[10px]" style="color: var(--color-text-faint);">
							{m.agent_editor_test_tools({ tools: t.tools.join(', ') })}
						</div>
					{/if}
					{#if t.error}
						<div
							class="rounded-md px-2 py-1.5"
							style="background: var(--color-danger-soft); color: var(--color-danger);"
						>
							{t.error}
						</div>
					{:else}
						<div
							class="rounded-md border px-2 py-1.5 whitespace-pre-wrap"
							style="border-color: var(--color-border); color: var(--color-text);"
						>
							{t.answer || (t.pending ? '…' : '')}
						</div>
					{/if}
				</div>
			{/each}
		</div>
		<form
			class="mt-2 flex gap-1.5"
			onsubmit={(e) => {
				e.preventDefault();
				send();
			}}
		>
			<input
				class="field-input flex-1 text-xs"
				placeholder={m.agent_editor_test_placeholder()}
				bind:value={input}
				disabled={busy}
			/>
			{#if busy}
				<Button variant="secondary" size="sm" type="button" onclick={() => controller?.abort()}>
					<Square size={12} />
				</Button>
			{:else}
				<Button size="sm" type="submit" disabled={!input.trim()}>
					<Send size={12} />
				</Button>
			{/if}
		</form>
	{/if}
</div>
