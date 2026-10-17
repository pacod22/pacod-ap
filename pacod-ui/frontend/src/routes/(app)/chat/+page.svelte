<script lang="ts">
	import { tick, onMount, onDestroy } from 'svelte';
	import { browser } from '$app/environment';
	import { page } from '$app/state';
	import { afterNavigate, goto } from '$app/navigation';
	import {
		Send,
		Square,
		ThumbsUp,
		ThumbsDown,
		Wrench,
		Sparkles,
		ChevronRight,
		ChevronDown,
		BookOpen,
		AlertCircle,
		AlertTriangle,
		Info,
		MessageSquare,
		Plus,
		Clock,
		PanelLeft,
		Brain,
		Hash,
		Trash2,
		Copy,
		Check,
		RotateCcw
	} from 'lucide-svelte';
	import {
		streamChat,
		submitFeedback,
		submitResponseFeedback,
		fetchSessions,
		fetchSessionTurns,
		deleteSession,
		resolveChatError,
		isAbortError,
		type CitationEvent,
		type ChatErrorInfo,
		type ConversationMessage,
		type SessionSummary,
		type TokenUsage
	} from '$lib/features/chat/index';
	import { marked } from 'marked';
	import DOMPurify from 'dompurify';
	import { m } from '$lib/paraglide/messages';
	import { getLocale } from '$lib/paraglide/runtime';
	import { shortNameFor } from '$lib/features/profiles/queries';

	// Model output is untrusted. marked passes raw HTML through (its `sanitize`
	// option is long gone), and this page re-renders stored answers from history,
	// so one poisoned answer would run on every reload. Two layers: the link
	// renderer refuses non-web schemes and escapes its attributes, and the final
	// HTML goes through DOMPurify before {@html}.
	const SAFE_HREF = /^(https?:|mailto:)/i;
	const escapeAttr = (s: string) =>
		s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
	marked.use({
		breaks: true,
		renderer: {
			link({ href, title, text }) {
				if (!SAFE_HREF.test(href ?? '')) return text;
				const t = title ? ` title="${escapeAttr(title)}"` : '';
				return `<a href="${escapeAttr(href)}"${t} target="_blank" rel="noopener noreferrer">${text}</a>`;
			}
		}
	});
	function renderMarkdown(md: string): string {
		if (!browser) return ''; // turns only exist client-side; never trust an SSR pass
		return DOMPurify.sanitize(marked.parse(md, { async: false }) as string, {
			USE_PROFILES: { html: true },
			ADD_ATTR: ['target', 'rel']
		});
	}

	let { data } = $props();

	// ---- Session ---------------------------------------------------------
	let sessionId = $state<string>(crypto.randomUUID());
	let activeSessionId = $state<string | null>(null); // null = new unsaved chat

	// ---- Profile (agent) -------------------------------------------------
	// The agents this user may chat with come from the layout load (SSR). Each is
	// a separate agent with its own KB + chat history, and is listed as its own
	// sidebar entry (→ /chat?agent=<id>). The `agent` URL param is the single
	// source of truth for which one is active; a bare /chat is redirected to the
	// last-used (or default) agent below, so the app always opens on an agent.
	let profiles = $derived(data.myProfiles ?? []);
	let activeProfileId = $state<string | null>(null);
	const PROFILE_STORAGE_KEY = 'chat:profileId';

	let activeProfile = $derived(profiles.find((p) => p.id === activeProfileId) ?? null);
	// Admins can chat with an agent's agent-editor DEV deployment (the backend
	// 403s the dev variant for anyone else, and only admins get the flag).
	let useDev = $state(false);
	let devAvailable = $derived(!!activeProfile?.has_dev_deployment);
	// A never-published agent has no production version: the backend runs its
	// dev deployment for a 'prod' request (or refuses with not_released), so
	// there is nothing to switch — show a fixed DEV badge instead.
	let unreleased = $derived(activeProfile?.release_state === 'dev');
	let agentVariant = $derived<'prod' | 'dev'>(
		!unreleased && useDev && devAvailable ? 'dev' : 'prod'
	);

	// Profile-dependent, localized chat copy. Each agent gets its own welcome +
	// input-placeholder message keyed by slug (`chat_welcome_<slug>` /
	// `chat_input_placeholder_<slug>`). Any profile without a slug-specific key —
	// e.g. an admin-created one, or a hyphenated slug that can't be a Paraglide
	// message name — falls back to a parameterized generic that names the agent;
	// only a profile-less state (no agents) hits the original constant.
	const msg = m as unknown as Record<
		string,
		((inputs?: Record<string, string>) => string) | undefined
	>;
	function welcomeText(): string {
		const p = activeProfile;
		const specific = p ? msg[`chat_welcome_${p.slug}`] : undefined;
		if (specific) return specific();
		if (p) return m.chat_welcome_generic({ name: shortNameFor(p, getLocale()) });
		return m.chat_welcome();
	}
	function inputPlaceholder(): string {
		const p = activeProfile;
		const specific = p ? msg[`chat_input_placeholder_${p.slug}`] : undefined;
		if (specific) return specific();
		if (p) return m.chat_input_placeholder_generic({ name: shortNameFor(p, getLocale()) });
		return m.chat_input_placeholder();
	}

	/** Which agent should be active, given the URL param then stored preference. */
	function resolveAgentId(): string | null {
		if (profiles.length === 0) return null;
		const urlAgent = page.url.searchParams.get('agent');
		if (urlAgent && profiles.some((p) => p.id === urlAgent)) return urlAgent;
		const stored =
			typeof localStorage !== 'undefined' ? localStorage.getItem(PROFILE_STORAGE_KEY) : null;
		if (stored && profiles.some((p) => p.id === stored)) return stored;
		return profiles[0].id;
	}

	// Sync active agent from the URL on every navigation (including initial load).
	// afterNavigate fires once per navigation and gives us `from` (null on the
	// first load), so we can reset the conversation only on a real agent switch —
	// this avoids the self-triggering feedback loop of a $effect on page.url.
	afterNavigate(({ from }) => {
		const target = resolveAgentId();
		if (target === null) {
			activeProfileId = null;
			return;
		}
		if (target !== activeProfileId) {
			const isSwitch = activeProfileId !== null && from !== null;
			activeProfileId = target;
			if (typeof localStorage !== 'undefined') localStorage.setItem(PROFILE_STORAGE_KEY, target);
			// A user-initiated switch to a different agent starts a fresh chat.
			// (reloadSessions runs via the $effect watching activeProfileId.)
			if (isSwitch) startNewChat();
		}
		// Keep the URL carrying the active agent so the sidebar highlights it and
		// the chat is deep-linkable — replace (don't push) so Back still works.
		if (page.url.searchParams.get('agent') !== target) {
			const u = new URL(page.url);
			u.searchParams.set('agent', target);
			goto(u, { replaceState: true, keepFocus: true, noScroll: true });
		}
	});

	/** Switch the active agent by navigating; afterNavigate applies the change. */
	function switchProfile(id: string) {
		if (streaming || id === activeProfileId) return;
		const u = new URL(page.url);
		u.searchParams.set('agent', id);
		u.searchParams.delete('session'); // drop any open-session deep link
		goto(u, { keepFocus: true, noScroll: true });
	}

	// ---- Chat history sidebar --------------------------------------------
	let historyOpen = $state(true);
	let days = $state(30);
	let sessions = $state<SessionSummary[]>([]);
	let loadingSessions = $state(false);
	let loadingSession = $state(false);

	let historyError = $state(false);
	// Monotonic request tokens: two quick agent switches must not let the older
	// response land last and show another agent's history.
	let sessionsReq = 0;
	let openReq = 0;

	async function reloadSessions() {
		const req = ++sessionsReq;
		loadingSessions = true;
		try {
			const list = await fetchSessions(days, activeProfileId);
			if (req !== sessionsReq) return; // superseded
			sessions = list;
			historyError = false;
		} catch {
			if (req === sessionsReq) historyError = true;
		} finally {
			if (req === sessionsReq) loadingSessions = false;
		}
	}

	$effect(() => {
		void days; // re-run when days changes
		void activeProfileId; // re-run when the active agent changes
		reloadSessions();
	});

	// Deep-link: /chat?session=<id> (e.g. from the dashboard) opens that chat.
	onMount(() => {
		// Collapse the history drawer by default on small screens (it overlays
		// the conversation there). Keep it open on large screens.
		if (typeof window !== 'undefined' && !window.matchMedia('(min-width: 1024px)').matches) {
			historyOpen = false;
		}
		// The active agent is resolved from the URL in afterNavigate (which also
		// fires on this initial load); here we only handle the ?session deep link.
		const sid = page.url.searchParams.get('session');
		if (sid) openSession(sid);
	});

	/** Close the history drawer on small screens (where it overlays the chat). */
	function closeHistoryOnMobile() {
		if (typeof window !== 'undefined' && !window.matchMedia('(min-width: 1024px)').matches) {
			historyOpen = false;
		}
	}

	// Group sessions by recency for display.
	const DAY_MS = 86_400_000;
	function groupSessions(list: SessionSummary[]) {
		const todayStart = new Date();
		todayStart.setHours(0, 0, 0, 0);
		const todayMs = todayStart.getTime();

		const buckets = [
			{ label: m.chat_today(), cutoff: todayMs },
			{ label: m.chat_yesterday(), cutoff: todayMs - DAY_MS },
			{ label: m.chat_this_week(), cutoff: todayMs - 7 * DAY_MS },
			{ label: m.chat_earlier(), cutoff: 0 }
		];

		const groups: { label: string; items: SessionSummary[] }[] = buckets.map((b) => ({
			label: b.label,
			items: []
		}));

		for (const s of list) {
			const ms = new Date(s.last_turn_at).getTime();
			const idx = buckets.findIndex((b) => ms >= b.cutoff);
			if (idx !== -1) groups[idx].items.push(s);
		}
		return groups.filter((g) => g.items.length > 0);
	}

	let groupedSessions = $derived(groupSessions(sessions));

	function truncate(text: string, max = 52) {
		return text.length > max ? text.slice(0, max).trimEnd() + '…' : text;
	}

	async function startNewChat() {
		if (streaming) return;
		closeHistoryOnMobile();
		turns = [];
		sessionId = crypto.randomUUID();
		activeSessionId = null;
		inputValue = '';
		if (inputEl) inputEl.style.height = 'auto';
	}

	async function openSession(sid: string) {
		if (streaming || loadingSession) return;
		closeHistoryOnMobile();
		const req = ++openReq;
		loadingSession = true;
		try {
			const raw = await fetchSessionTurns(sid, activeProfileId);
			if (req !== openReq) return; // superseded by a later click
			turns = raw.flatMap((t) => [
				{ role: 'user', content: t.user_message } satisfies UserTurn,
				{
					role: 'assistant',
					content: t.response ?? '',
					turn_id: t.turn_id,
					query: t.user_message,
					citations: (t.citations ?? []).map((c) => ({ type: 'citation' as const, ...c })),
					tool_names: t.tool_names ?? [],
					suggestions: [],
					is_thinking: false,
					used_thinking: false,
					is_done: true,
					error: null,
					duration_ms: t.duration_ms ?? null,
					usage: t.usage ?? null,
					feedback: {},
					feedback_pending: {},
					response_vote: null,
					response_vote_pending: false,
					copied: false,
					sources_open: false
				} satisfies AssistantTurn
			]);
			sessionId = sid;
			activeSessionId = sid;
			historyError = false;
		} catch {
			if (req === openReq) historyError = true;
		} finally {
			if (req === openReq) loadingSession = false;
		}
	}

	// session_id currently being deleted, so its row can show a pending state.
	let deletingSessionId = $state<string | null>(null);

	async function deleteChat(sid: string) {
		if (streaming || deletingSessionId) return;
		if (!confirm(m.chat_delete_confirm())) return;
		deletingSessionId = sid;
		try {
			await deleteSession(sid);
			sessions = sessions.filter((s) => s.session_id !== sid);
			// If the deleted chat is the one open in the pane, reset to a fresh chat
			// so new messages don't land under a now-hidden session_id.
			if (activeSessionId === sid || sessionId === sid) {
				turns = [];
				sessionId = crypto.randomUUID();
				activeSessionId = null;
			}
		} catch {
			// Surface nothing intrusive; leave the row in place on failure.
		} finally {
			deletingSessionId = null;
		}
	}

	// ---- Turn model ------------------------------------------------------

	interface UserTurn {
		role: 'user';
		content: string;
	}

	interface AssistantTurn {
		role: 'assistant';
		content: string;
		turn_id: string | null;
		query: string;
		citations: CitationEvent[];
		tool_names: string[];
		suggestions: string[];
		is_thinking: boolean;
		// Latches true once an extended-thinking event arrives, so a complex turn
		// stays visibly marked even after thinking finishes.
		used_thinking: boolean;
		is_done: boolean;
		error: ChatErrorInfo | null;
		// Post-turn summary: end-to-end response time and token spend. Null until
		// the `done` event arrives (or for legacy history turns lacking the data).
		duration_ms: number | null;
		usage: TokenUsage | null;
		// chunk_id → relevance (1 or -1), set after successful submission
		feedback: Record<string, 1 | -1>;
		// chunk_ids currently awaiting server confirmation
		feedback_pending: Record<string, boolean>;
		// Response-level like/dislike (applies to all cited KB chunks); UI + feedback.
		response_vote: 1 | -1 | null;
		response_vote_pending: boolean;
		// Transient "copied to clipboard" flag for the copy button.
		copied: boolean;
		// Whether the citation card grid is unfolded (collapsed by default).
		sources_open: boolean;
	}

	type Turn = UserTurn | AssistantTurn;

	let turns = $state<Turn[]>([]);
	// Incremented on every token so the scroll effect re-runs while streaming.
	let scrollTick = $state(0);

	// ---- Auto-scroll -----------------------------------------------------
	let messagesEl = $state<HTMLElement | null>(null);

	$effect(() => {
		// Track both new messages and streaming tokens.
		void turns.length;
		void scrollTick;
		tick().then(() => {
			if (!messagesEl) return;
			const { scrollTop, scrollHeight, clientHeight } = messagesEl;
			// Only auto-scroll if the user is already near the bottom.
			if (scrollHeight - scrollTop - clientHeight < 200) {
				messagesEl.scrollTop = scrollHeight;
			}
		});
	});

	// ---- Input -----------------------------------------------------------
	let inputValue = $state('');
	let inputEl = $state<HTMLTextAreaElement | null>(null);
	let streaming = $state(false);
	let abortController: AbortController | null = null;

	// Navigating away mid-answer used to leave the SSE fetch running (and its
	// callback mutating the turns of a destroyed component) for up to the edge
	// timeout. The stream belongs to this page; end it with the page.
	onDestroy(() => abortController?.abort());

	function abort() {
		abortController?.abort();
		abortController = null;
		// Mark the in-progress turn as done via the reactive proxy.
		const last = turns.at(-1);
		if (last?.role === 'assistant' && !last.is_done) {
			last.is_thinking = false;
			last.is_done = true;
		}
		streaming = false;
	}

	// ---- Send ------------------------------------------------------------
	async function send(message: string) {
		message = message.trim();
		if (!message || streaming) return;

		// Language hint for localizing error messages, inferred from the user's
		// message (Cyrillic → Bulgarian). Used when an error has no localized body.
		const lang: 'bg' | 'en' = /[Ѐ-ӿ]/.test(message) ? 'bg' : 'en';

		inputValue = '';
		if (inputEl) inputEl.style.height = 'auto';

		// Build history from all turns that have completed.
		const history: ConversationMessage[] = turns
			.filter((t) => t.role === 'user' || (t.role === 'assistant' && t.is_done))
			.map((t) => ({ role: t.role, content: t.content }));

		turns.push({ role: 'user', content: message } satisfies UserTurn);
		turns.push({
			role: 'assistant',
			content: '',
			turn_id: null,
			query: message,
			citations: [],
			tool_names: [],
			suggestions: [],
			is_thinking: false,
			used_thinking: false,
			is_done: false,
			error: null,
			duration_ms: null,
			usage: null,
			feedback: {},
			feedback_pending: {},
			response_vote: null,
			response_vote_pending: false,
			copied: false,
			sources_open: false
		} satisfies AssistantTurn);

		// Save the index so the streaming callback always accesses the reactive
		// proxy via turns[asstIdx] rather than a stale local reference.
		// (In Svelte 5, turns.push() stores a proxied copy; the original object
		// reference is no longer reactive after the push.)
		const asstIdx = turns.length - 1;
		const currentSessionId = sessionId;

		streaming = true;
		abortController = new AbortController();

		try {
			await streamChat(
				{
					message,
					session_id: currentSessionId,
					history,
					profile_id: activeProfileId,
					agent_variant: agentVariant
				},
				(event) => {
					// Always resolve through the array to get the reactive proxy.
					const asst = turns[asstIdx] as AssistantTurn;
					switch (event.type) {
						case 'token':
							asst.content += event.text;
							scrollTick++;
							break;
						case 'tool_call':
							if (!asst.tool_names.includes(event.name)) {
								asst.tool_names.push(event.name);
							}
							break;
						case 'citation':
							if (!asst.citations.some((c) => c.source_id === event.source_id)) {
								asst.citations.push(event);
							}
							break;
						case 'thinking':
							asst.is_thinking = event.status === 'start';
							if (event.status === 'start') asst.used_thinking = true;
							break;
						case 'suggestions':
							asst.suggestions = event.questions;
							break;
						case 'done':
							asst.turn_id = event.turn_id;
							asst.is_thinking = false;
							asst.is_done = true;
							asst.duration_ms = event.duration_ms ?? null;
							asst.usage = event.usage ?? null;
							activeSessionId = currentSessionId;
							break;
					}
				},
				abortController.signal
			);
		} catch (e) {
			const asst = turns[asstIdx] as AssistantTurn;
			asst.is_thinking = false;
			asst.is_done = true;
			// User pressed Stop — not an error; leave the partial answer as-is.
			if (!isAbortError(e)) {
				asst.error = resolveChatError(e, lang);
			}
		} finally {
			streaming = false;
			abortController = null;
			reloadSessions();
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send(inputValue);
		}
	}

	function autoResize(e: Event) {
		const el = e.currentTarget as HTMLTextAreaElement;
		el.style.height = 'auto';
		el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
	}

	function useSuggestion(q: string) {
		inputValue = q;
		send(q);
	}

	// ---- Feedback --------------------------------------------------------
	async function vote(asst: AssistantTurn, citation: CitationEvent, relevance: 1 | -1) {
		if (!asst.turn_id || !citation.chunk_id) return;
		if (asst.feedback[citation.chunk_id] !== undefined) return;
		if (asst.feedback_pending[citation.chunk_id]) return;

		asst.feedback_pending[citation.chunk_id] = true;
		try {
			await submitFeedback({
				session_id: sessionId,
				turn_id: asst.turn_id,
				query: asst.query,
				chunk_id: citation.chunk_id,
				source_id: citation.source_id,
				relevance,
				profile_id: activeProfileId
			});
			asst.feedback[citation.chunk_id] = relevance;
		} catch {
			// Non-critical — silently ignore.
		} finally {
			delete asst.feedback_pending[citation.chunk_id];
		}
	}

	// ---- Response actions (like / dislike / copy / redo) -----------------
	// Response-level like/dislike on the whole answer — its own feedback channel
	// (response_feedback table), independent of citations. Re-voting upserts.
	async function voteResponse(asst: AssistantTurn, rating: 1 | -1) {
		if (!asst.turn_id || asst.response_vote_pending) return;
		if (asst.response_vote === rating) return; // already voted this way
		asst.response_vote_pending = true;
		try {
			await submitResponseFeedback({
				session_id: sessionId,
				turn_id: asst.turn_id,
				query: asst.query,
				rating,
				response: asst.content,
				profile_id: activeProfileId
			});
			asst.response_vote = rating;
		} catch {
			// Non-critical — silently ignore.
		} finally {
			asst.response_vote_pending = false;
		}
	}

	async function copyResponse(asst: AssistantTurn) {
		try {
			await navigator.clipboard.writeText(asst.content);
			asst.copied = true;
			setTimeout(() => {
				asst.copied = false;
			}, 1500);
		} catch {
			// Clipboard unavailable (e.g. insecure context) — ignore.
		}
	}

	// Regenerate the last answer: drop it and its user turn, then re-send the same
	// question so it streams a fresh response with the same prior history.
	async function regenerate(i: number) {
		if (streaming) return;
		const asst = turns[i];
		if (asst.role !== 'assistant') return;
		const query = asst.query;
		turns = turns.slice(0, i - 1);
		await send(query);
	}

	// ---- Helpers ---------------------------------------------------------
	// Calm, on-palette source styling (Fiscus): authoritative sources read teal,
	// external/web reads gold, neutral sources stay muted. All token-driven so
	// they follow the light/dark theme.
	const SOURCE_COLOURS: Record<string, string> = {
		law: 'color: var(--color-brand-muted); background: var(--color-brand-soft);',
		guidance: 'color: var(--color-brand-muted); background: var(--color-brand-soft);',
		rate_table: 'color: var(--color-brand); background: var(--color-brand-soft);',
		web: 'color: var(--color-warning); background: var(--color-gold-soft);',
		structured: 'color: var(--color-text-muted); background: var(--color-surface-3);',
		document: 'color: var(--color-text-muted); background: var(--color-surface-3);'
	};

	function sourceStyle(t: string) {
		return SOURCE_COLOURS[t] ?? SOURCE_COLOURS.document;
	}

	// Error block styling by severity (token-driven, follows the theme).
	function errorStyle(severity: ChatErrorInfo['severity']): string {
		if (severity === 'info')
			return 'background: var(--color-brand-soft); color: var(--color-brand-muted);';
		if (severity === 'warning')
			return 'background: var(--color-gold-soft); color: var(--color-warning);';
		return 'background: var(--color-danger-soft); color: var(--color-danger);';
	}

	// Localized "try again" label, matching the (localized) error message language.
	function retryLabel(info: ChatErrorInfo): string {
		return /[Ѐ-ӿ]/.test(info.message) ? 'Опитайте отново' : 'Try again';
	}

	const TOOL_LABELS: Record<string, string> = {
		kb_search: m.chat_tool_kb_search(),
		get_fact: m.chat_tool_get_fact(),
		list_facts: m.chat_tool_list_facts(),
		web_search: m.chat_tool_web_search()
	};

	function toolLabel(name: string) {
		return TOOL_LABELS[name] ?? name;
	}

	// ---- Per-turn summary formatting -------------------------------------
	function formatDuration(ms: number): string {
		return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
	}

	function totalTokens(usage: TokenUsage | null): number | null {
		if (!usage) return null;
		return (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0);
	}

	// True when a turn carries at least one summary metric worth showing.
	function hasSummary(turn: AssistantTurn): boolean {
		return turn.duration_ms != null || turn.usage != null || turn.tool_names.length > 0;
	}
</script>

<svelte:head><title>{m.chat_title()}</title></svelte:head>

<!-- Fill the entire main area (undo the responsive padding from AppShell) -->
<div class="-m-4 flex h-[calc(100dvh-5.5rem)] flex-col sm:-m-6">
	<!-- Chrome bar -->
	<div
		class="flex shrink-0 items-center justify-between border-b px-4 py-1.5"
		style="background: var(--color-surface-2); border-color: var(--color-border);"
	>
		<!-- Left: sidebar toggle + chat label -->
		<div class="flex items-center gap-1">
			<button
				onclick={() => (historyOpen = !historyOpen)}
				aria-label={historyOpen ? m.chat_hide_history() : m.chat_show_history()}
				title={historyOpen ? m.chat_hide_history() : m.chat_show_history()}
				class="rounded-lg p-1.5 transition-colors"
				style={historyOpen
					? 'background: var(--color-surface-3); color: var(--color-text);'
					: 'color: var(--color-text-muted);'}
			>
				<PanelLeft size={15} />
			</button>
			<span
				class="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium"
				style="background: var(--color-surface-3); color: var(--color-text);"
			>
				<MessageSquare size={13} />
				{m.nav_chat()}
			</span>
		</div>

		<!-- Right-side: agent picker + brand pill -->
		<div class="flex items-center gap-2">
			{#if profiles.length > 0}
				{#if profiles.length === 1}
					<span
						class="rounded-md px-2 py-0.5 text-[11px] font-medium"
						style="background: var(--color-surface-3); color: var(--color-text-muted);"
						title={m.chat_active_agent()}
					>
						{shortNameFor(activeProfile ?? profiles[0], getLocale())}
					</span>
				{:else}
					<select
						value={activeProfileId}
						onchange={(e) => switchProfile((e.currentTarget as HTMLSelectElement).value)}
						disabled={streaming}
						class="rounded-md px-2 py-0.5 text-[11px] font-medium outline-none disabled:opacity-40"
						style="background: var(--color-surface-3); color: var(--color-text-muted); border: none; cursor: pointer;"
						title={m.chat_choose_agent()}
					>
						{#each profiles as p (p.id)}
							<option value={p.id}
								>{shortNameFor(p, getLocale())}{p.release_state === 'dev'
									? ` · ${m.chat_agent_unreleased()}`
									: ''}</option
							>
						{/each}
					</select>
				{/if}
				{#if unreleased}
					<span
						class="rounded-md px-2 py-0.5 text-[10px] font-semibold"
						style="background: var(--color-warning-soft); color: var(--color-warning);"
						title={m.chat_agent_unreleased_title()}>{m.chat_dev_variant_on()}</span
					>
				{:else if devAvailable}
					<button
						type="button"
						class="rounded-md px-2 py-0.5 text-[10px] font-semibold disabled:opacity-40"
						style="background: {useDev ? 'var(--color-warning-soft)' : 'var(--color-surface-3)'};
							color: {useDev ? 'var(--color-warning)' : 'var(--color-text-faint)'};"
						aria-pressed={useDev}
						disabled={streaming}
						title={m.chat_dev_variant_title()}
						onclick={() => (useDev = !useDev)}
					>
						{useDev ? m.chat_dev_variant_on() : m.chat_dev_variant_off()}
					</button>
				{/if}
			{/if}
			<span
				class="rounded-full px-2 py-0.5 text-[10px] font-medium"
				style="background: var(--color-brand-soft); color: var(--color-brand);"
			>
				AI
			</span>
		</div>
	</div>

	<!-- Native chat -->
	<div class="relative flex min-h-0 flex-1">
		<!-- Mobile backdrop for the history drawer -->
		{#if historyOpen}
			<button
				class="absolute inset-0 z-20 bg-black/40 lg:hidden"
				aria-label={m.chat_close_history()}
				onclick={() => (historyOpen = false)}
			></button>
		{/if}

		<!-- ---- History sidebar (overlay drawer on mobile, in-flow on lg) ---- -->
		{#if historyOpen}
			<div
				class="absolute inset-y-0 left-0 z-30 flex w-72 max-w-[85%] shrink-0 flex-col border-r shadow-xl lg:static lg:w-56 lg:max-w-none lg:shadow-none"
				style="background: var(--color-surface-2); border-color: var(--color-border);"
			>
				<!-- Header -->
				<div
					class="flex shrink-0 items-center justify-between border-b px-3 py-2"
					style="border-color: var(--color-border);"
				>
					<span
						class="font-mono text-[10px] font-semibold tracking-[0.14em] uppercase"
						style="color: var(--color-text-faint);">History</span
					>
					<div class="flex items-center gap-1">
						<!-- Days filter -->
						<select
							bind:value={days}
							class="rounded-md px-1.5 py-0.5 text-[11px] font-medium outline-none"
							style="background: var(--color-surface-3); color: var(--color-text-muted); border: none; cursor: pointer;"
							title={m.chat_show_last_days()}
						>
							<option value={7}>7 d</option>
							<option value={30}>30 d</option>
							<option value={90}>90 d</option>
							<option value={365}>1 y</option>
						</select>
						<!-- New chat -->
						<button
							onclick={startNewChat}
							disabled={streaming}
							aria-label={m.chat_new_chat()}
							title={m.chat_new_chat()}
							class="rounded-md p-1 transition-colors hover:bg-[var(--color-surface-3)] disabled:opacity-40"
							style="color: var(--color-text-muted);"
						>
							<Plus size={13} />
						</button>
					</div>
				</div>

				<!-- Session list -->
				<div class="flex-1 overflow-y-auto py-1">
					{#if loadingSessions}
						<div class="px-3 py-6 text-center text-[11px]" style="color: var(--color-text-muted);">
							{m.common_loading()}
						</div>
					{:else if historyError}
						<div class="px-3 py-6 text-center text-[11px]" style="color: var(--color-danger);">
							{m.common_failed_to_load()}
						</div>
					{:else if groupedSessions.length === 0}
						<div class="px-3 py-6 text-center text-[11px]" style="color: var(--color-text-muted);">
							{m.chat_no_chats({ days })}
						</div>
					{:else}
						{#each groupedSessions as group (group.label)}
							<p
								class="px-3 pt-3 pb-0.5 font-mono text-[10px] font-semibold tracking-[0.14em] uppercase"
								style="color: var(--color-text-faint);"
							>
								{group.label}
							</p>
							{#each group.items as s (s.session_id)}
								{@const isActive = s.session_id === activeSessionId}
								<div
									class="group relative rounded-lg transition-colors hover:bg-[var(--color-surface-3)]"
									style={isActive ? 'background: var(--color-brand-soft);' : ''}
								>
									<button
										onclick={() => openSession(s.session_id)}
										disabled={loadingSession || streaming || deletingSessionId === s.session_id}
										class="flex w-full flex-col gap-0.5 rounded-lg px-3 py-2 pr-9 text-left disabled:opacity-50"
										style={isActive ? 'color: var(--color-brand);' : 'color: var(--color-text);'}
									>
										<span class="line-clamp-2 text-[12px] leading-snug font-medium">
											{truncate(s.title)}
										</span>
										<span
											class="flex items-center gap-1 text-[10px]"
											style="color: {isActive
												? 'var(--color-brand-muted)'
												: 'var(--color-text-muted)'};"
										>
											<Clock size={9} />
											{s.turn_count} turn{s.turn_count !== 1 ? 's' : ''}
										</span>
									</button>
									<button
										onclick={() => deleteChat(s.session_id)}
										disabled={streaming || deletingSessionId === s.session_id}
										aria-label={m.chat_delete_chat()}
										title={m.chat_delete_chat()}
										class="absolute top-1.5 right-1.5 rounded-md p-1.5 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-[var(--color-danger-soft)] hover:text-[var(--color-danger)] focus:opacity-100 disabled:opacity-50"
										style="color: var(--color-text-muted);"
									>
										<Trash2 size={13} />
									</button>
								</div>
							{/each}
						{/each}
					{/if}
				</div>
			</div>
		{/if}

		<!-- ---- Chat main area ---- -->
		<div class="flex min-h-0 flex-1 flex-col">
			<!-- Messages -->
			<div
				bind:this={messagesEl}
				class="flex-1 overflow-y-auto px-4 py-6"
				style="background: var(--color-surface);"
			>
				<div class="mx-auto max-w-3xl space-y-6">
					{#if turns.length === 0}
						<div class="flex flex-col items-center justify-center gap-3 py-24 text-center">
							<div
								class="grid h-12 w-12 place-items-center rounded-2xl"
								style="background: var(--color-brand-soft);"
							>
								<Sparkles size={22} style="color: var(--color-brand);" />
							</div>
							<p class="text-base font-medium">{m.chat_greeting()}</p>
							<p class="max-w-xs text-sm text-[var(--color-text-muted)]">
								{welcomeText()}
							</p>
						</div>
					{/if}

					{#each turns as turn, i (i)}
						{#if turn.role === 'user'}
							<!-- User message -->
							<div class="flex justify-end">
								<div
									class="max-w-xl rounded-2xl rounded-br-sm px-4 py-3 text-sm leading-relaxed"
									style="background: var(--color-brand); color: white;"
								>
									{turn.content}
								</div>
							</div>
						{:else}
							<!-- Assistant message -->
							<div class="flex flex-col gap-3">
								<!-- Tool calls + extended-thinking marker -->
								{#if turn.tool_names.length > 0 || turn.used_thinking}
									<div class="flex flex-wrap gap-1.5">
										{#if turn.used_thinking}
											<span
												class="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium"
												style="background: var(--color-brand-soft); color: var(--color-brand);"
												title={m.chat_extended_thinking()}
											>
												<Brain size={10} />
												{m.chat_extended_thinking_label()}
											</span>
										{/if}
										{#each turn.tool_names as name}
											<span
												class="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium"
												style="background: var(--color-surface-3); color: var(--color-text-muted);"
											>
												<Wrench size={10} />
												{toolLabel(name)}
											</span>
										{/each}
									</div>
								{/if}

								<!-- Working / thinking status — shows from send until the first
							     content streams in, so the user always sees progress. -->
								{#if !turn.is_done && !turn.content && !turn.error}
									<div class="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
										<span class="flex gap-1">
											{#each [0, 1, 2] as d}
												<span
													class="inline-block h-1.5 w-1.5 animate-bounce rounded-full"
													style="background: var(--color-brand); animation-delay: {d * 120}ms;"
												></span>
											{/each}
										</span>
										{#if turn.is_thinking}
											<span
												class="inline-flex items-center gap-1.5 font-medium"
												style="color: var(--color-brand);"
											>
												<Brain size={13} />
												{m.chat_thinking()}
											</span>
										{:else if turn.tool_names.length > 0}
											{m.chat_looking_sources()}
										{:else}
											{m.chat_working()}
										{/if}
									</div>
								{/if}

								<!-- Response text -->
								{#if turn.content}
									<div class="prose prose-sm chat-prose max-w-none">
										<!-- DOMPurify-sanitised in renderMarkdown -->
										<!-- eslint-disable-next-line svelte/no-at-html-tags -->
										{@html renderMarkdown(turn.content)}
									</div>
									{#if !turn.is_done}
										<span
											class="mt-0.5 inline-block h-4 w-0.5 animate-pulse"
											style="background: var(--color-brand);"
										></span>
									{/if}
								{/if}

								<!-- Response actions: like / dislike / copy / redo -->
								{#if turn.is_done && turn.content && !turn.error}
									<div
										class="-ml-1.5 flex items-center gap-0.5 pt-0.5"
										style="color: var(--color-text-muted);"
									>
										<button
											onclick={() => voteResponse(turn as AssistantTurn, 1)}
											disabled={turn.response_vote_pending || !turn.turn_id}
											aria-label={m.chat_like_response()}
											title={m.chat_like()}
											class="rounded-md p-1.5 transition-colors hover:text-[var(--color-text)] disabled:cursor-default disabled:opacity-40"
											style={turn.response_vote === 1
												? 'color: var(--color-success); background: var(--color-success-soft);'
												: ''}
										>
											<ThumbsUp size={13} />
										</button>
										<button
											onclick={() => voteResponse(turn as AssistantTurn, -1)}
											disabled={turn.response_vote_pending || !turn.turn_id}
											aria-label={m.chat_dislike_response()}
											title={m.chat_dislike()}
											class="rounded-md p-1.5 transition-colors hover:text-[var(--color-text)] disabled:cursor-default disabled:opacity-40"
											style={turn.response_vote === -1
												? 'color: var(--color-danger); background: var(--color-danger-soft);'
												: ''}
										>
											<ThumbsDown size={13} />
										</button>
										<button
											onclick={() => copyResponse(turn as AssistantTurn)}
											aria-label={m.chat_copy_response()}
											title={turn.copied ? m.chat_copied() : m.chat_copy()}
											class="rounded-md p-1.5 transition-colors hover:text-[var(--color-text)]"
											style={turn.copied ? 'color: var(--color-success);' : ''}
										>
											{#if turn.copied}<Check size={13} />{:else}<Copy size={13} />{/if}
										</button>
										{#if i === turns.length - 1}
											<button
												onclick={() => regenerate(i)}
												disabled={streaming}
												aria-label={m.chat_regenerate_response()}
												title={m.chat_regenerate()}
												class="rounded-md p-1.5 transition-colors hover:text-[var(--color-text)] disabled:cursor-default disabled:opacity-40"
											>
												<RotateCcw size={13} />
											</button>
										{/if}
									</div>
								{/if}

								<!-- Error -->
								{#if turn.error}
									<div
										class="flex items-start gap-2.5 rounded-lg px-3 py-2.5 text-sm"
										style={errorStyle(turn.error.severity)}
										role="alert"
									>
										{#if turn.error.severity === 'info'}
											<Info size={16} class="mt-0.5 shrink-0" />
										{:else if turn.error.severity === 'warning'}
											<AlertTriangle size={16} class="mt-0.5 shrink-0" />
										{:else}
											<AlertCircle size={16} class="mt-0.5 shrink-0" />
										{/if}
										<div class="min-w-0 flex-1 space-y-1">
											<p class="font-semibold">{turn.error.title}</p>
											<p class="opacity-90">{turn.error.message}</p>
											{#if turn.error.canRetry && i === turns.length - 1}
												<button
													onclick={() => regenerate(i)}
													disabled={streaming}
													class="mt-1 inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-opacity hover:opacity-80 disabled:opacity-50"
													style="border: 1px solid currentColor;"
												>
													<RotateCcw size={12} />
													{retryLabel(turn.error)}
												</button>
											{/if}
										</div>
									</div>
								{/if}

								<!-- Citations -->
								{#if turn.citations.length > 0}
									<div class="space-y-2">
										<button
											type="button"
											onclick={() => (turn.sources_open = !turn.sources_open)}
											aria-expanded={turn.sources_open}
											class="flex items-center gap-1.5 rounded-md py-1 pr-2 font-mono text-[10px] font-semibold tracking-[0.14em] uppercase transition-colors hover:text-[var(--color-text-muted)]"
											style="color: var(--color-text-faint);"
											title={turn.sources_open ? m.chat_sources_hide() : m.chat_sources_show()}
										>
											<BookOpen size={12} class="shrink-0" />
											<span>{m.chat_sources({ n: turn.citations.length })}</span>
											{#if turn.sources_open}
												<ChevronDown size={12} class="shrink-0" />
											{:else}
												<ChevronRight size={12} class="shrink-0" />
											{/if}
										</button>
										{#if turn.sources_open}
											<div class="flex flex-wrap gap-2">
												{#each turn.citations as citation (citation.source_id)}
													{@const chunkKey = citation.chunk_id ?? ''}
													{@const voted = turn.feedback[chunkKey]}
													{@const pending = !!turn.feedback_pending[chunkKey]}
													{@const canVote =
														!!citation.chunk_id &&
														!!turn.turn_id &&
														voted === undefined &&
														!pending}
													<div
														class="flex max-w-[260px] min-w-[180px] flex-col gap-1.5 rounded-xl border p-3 text-xs"
														style="background: var(--color-surface-2); border-color: var(--color-border);"
													>
														<div class="flex items-center gap-1.5">
															<span
																class="rounded-md px-1.5 py-0.5 text-[10px] font-semibold"
																style={sourceStyle(citation.source_type)}
															>
																{citation.source_type}
															</span>
															{#if citation.legal_year}
																<span
																	class="rounded-md px-1.5 py-0.5 text-[10px]"
																	style="background: var(--color-surface-3); color: var(--color-text-muted);"
																>
																	{citation.legal_year}
																</span>
															{/if}
														</div>

														{#if citation.url}
															<a
																href={citation.url}
																target="_blank"
																rel="noopener noreferrer"
																class="line-clamp-2 leading-snug font-medium hover:underline"
																style="color: var(--color-text);"
																title={citation.title}
															>
																{citation.title}
															</a>
														{:else}
															<span
																class="line-clamp-2 leading-snug font-medium"
																style="color: var(--color-text);"
																title={citation.title}
															>
																{citation.title}
															</span>
														{/if}

														{#if citation.chunk_id}
															<div class="mt-auto flex items-center justify-end gap-0.5 pt-1">
																<button
																	onclick={() => vote(turn as AssistantTurn, citation, 1)}
																	disabled={!canVote}
																	aria-label={m.chat_helpful()}
																	title={m.chat_helpful()}
																	class="rounded-md p-1.5 transition-colors"
																	style={voted === 1
																		? 'color: var(--color-success); background: var(--color-success-soft);'
																		: canVote
																			? 'color: var(--color-text-muted);'
																			: 'color: var(--color-text-muted); opacity: 0.35; cursor: default;'}
																>
																	<ThumbsUp size={12} />
																</button>
																<button
																	onclick={() => vote(turn as AssistantTurn, citation, -1)}
																	disabled={!canVote}
																	aria-label={m.chat_not_helpful()}
																	title={m.chat_not_helpful()}
																	class="rounded-md p-1.5 transition-colors"
																	style={voted === -1
																		? 'color: var(--color-danger); background: var(--color-danger-soft);'
																		: canVote
																			? 'color: var(--color-text-muted);'
																			: 'color: var(--color-text-muted); opacity: 0.35; cursor: default;'}
																>
																	<ThumbsDown size={12} />
																</button>
															</div>
														{/if}
													</div>
												{/each}
											</div>
										{/if}
									</div>
								{/if}

								<!-- Suggestions -->
								{#if turn.suggestions.length > 0 && turn.is_done}
									<div class="flex flex-wrap gap-2 pt-1">
										{#each turn.suggestions as q (q)}
											<button
												onclick={() => useSuggestion(q)}
												disabled={streaming}
												class="flex items-center gap-1 rounded-full border px-3 py-1.5 text-xs transition-colors hover:border-[var(--color-brand)] hover:text-[var(--color-brand)] disabled:opacity-50"
												style="border-color: var(--color-border); color: var(--color-text-muted);"
											>
												<ChevronRight size={11} class="shrink-0" />
												{q}
											</button>
										{/each}
									</div>
								{/if}

								<!-- Per-turn summary: response time · tools · tokens -->
								{#if turn.is_done && !turn.error && hasSummary(turn)}
									<div
										class="flex flex-wrap items-center gap-x-3 gap-y-1 pt-1 font-mono text-[11px]"
										style="color: var(--color-text-faint);"
									>
										{#if turn.duration_ms != null}
											<span class="inline-flex items-center gap-1" title={m.chat_response_time()}>
												<Clock size={11} />
												{formatDuration(turn.duration_ms)}
											</span>
										{/if}
										{#if turn.tool_names.length > 0}
											<span class="inline-flex items-center gap-1" title={m.conv_tools_used()}>
												<Wrench size={11} />
												{turn.tool_names.map(toolLabel).join(', ')}
											</span>
										{/if}
										{#if totalTokens(turn.usage) != null}
											<span class="inline-flex items-center gap-1" title={m.chat_tokens_used()}>
												<Hash size={11} />
												{totalTokens(turn.usage)!.toLocaleString()} tokens
											</span>
										{/if}
									</div>
								{/if}
							</div>
						{/if}
					{/each}
				</div>
			</div>

			<!-- Input -->
			<div
				class="shrink-0 border-t px-4 py-3"
				style="background: var(--color-surface-2); border-color: var(--color-border);"
			>
				<div class="mx-auto max-w-3xl">
					<div
						class="flex items-end gap-2 rounded-xl border px-3 py-2 transition-colors focus-within:border-[var(--color-brand)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-brand)_15%,transparent)]"
						style="background: var(--color-surface); border-color: var(--color-border);"
					>
						<textarea
							bind:this={inputEl}
							bind:value={inputValue}
							onkeydown={handleKeydown}
							oninput={autoResize}
							placeholder={inputPlaceholder()}
							rows={1}
							disabled={streaming}
							class="noborder min-h-[24px] flex-1 resize-none bg-transparent text-sm leading-relaxed outline-none placeholder:text-[var(--color-text-muted)] placeholder:opacity-70 disabled:opacity-60"
							style="max-height: 160px; color: var(--color-text);"></textarea>

						{#if streaming}
							<button
								onclick={abort}
								aria-label={m.chat_stop_generation()}
								title={m.chat_stop()}
								class="mb-0.5 shrink-0 rounded-lg p-1.5 transition-colors hover:bg-[var(--color-surface-3)]"
								style="color: var(--color-danger);"
							>
								<Square size={16} />
							</button>
						{:else}
							<button
								onclick={() => send(inputValue)}
								disabled={!inputValue.trim()}
								aria-label={m.chat_send_message()}
								title={m.chat_send()}
								class="mb-0.5 shrink-0 rounded-lg p-1.5 transition-colors disabled:opacity-40"
								style="background: var(--color-brand); color: white;"
							>
								<Send size={16} />
							</button>
						{/if}
					</div>
					<p class="mt-1.5 text-center text-[10px] text-[var(--color-text-muted)]">
						{m.chat_enter_hint()}
					</p>
				</div>
			</div>
			<!-- end chat main area -->
		</div>
		<!-- end sidebar + chat wrapper (native tab) -->
	</div>
	<!-- end root -->
</div>
