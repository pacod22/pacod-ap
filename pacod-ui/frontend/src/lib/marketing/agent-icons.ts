/**
 * Landing-card icons, by name. Static SVG path markup (24×24, stroke-based) —
 * the ONLY source of icon markup for agent cards: an agent's published data
 * names an icon (`marketing.icon`), it never carries SVG, so `{@html}` on these
 * strings is safe. Must stay in step with ICON_ALLOWLIST in
 * pacod-api/app/agent_editor/validate.py.
 */
export const AGENT_ICONS: Record<string, string> = {
	ledger: '<path d="M9 7h6M9 11h6M9 15h4" /><rect x="5" y="3" width="14" height="18" rx="2" />',
	building: '<path d="M3 21h18M5 21V9l7-5 7 5v12M9 21v-6h6v6" />',
	car: '<path d="M5 17h14l-1.5-6.5A2 2 0 0 0 15.6 9H8.4a2 2 0 0 0-1.9 1.5L5 17z" /><circle cx="7.5" cy="17.5" r="1.5" /><circle cx="16.5" cy="17.5" r="1.5" /><path d="M9 6h6" />',
	scale:
		'<path d="M12 3v18M5 21h14M6 7h12M6 7l-3 7a3 3 0 0 0 6 0L6 7zm12 0-3 7a3 3 0 0 0 6 0l-3-7z" />',
	home: '<path d="M3 11 12 4l9 7M5 10v10h14V10M10 20v-6h4v6" />',
	shield:
		'<path d="M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6l-7-3z" /><path d="m9 12 2 2 4-4" />',
	book: '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5z" /><path d="M8 7h7M8 11h7" />',
	chat: '<path d="M4 5h16v11H9l-5 4V5z" /><path d="M8 9h8M8 12h5" />',
	sparkles:
		'<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8L12 3z" /><path d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8.8-2z" />',
	briefcase:
		'<rect x="3" y="7" width="18" height="13" rx="2" /><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M3 13h18" />',
	heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z" />',
	eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" />'
};

export const AGENT_ICON_NAMES = Object.keys(AGENT_ICONS);
