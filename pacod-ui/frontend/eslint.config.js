import prettier from 'eslint-config-prettier';
import js from '@eslint/js';
import svelte from 'eslint-plugin-svelte';
import globals from 'globals';
import ts from 'typescript-eslint';

export default ts.config(
	js.configs.recommended,
	...ts.configs.recommended,
	...svelte.configs['flat/recommended'],
	prettier,
	...svelte.configs['flat/prettier'],
	{
		languageOptions: {
			globals: { ...globals.browser, ...globals.node }
		}
	},
	{
		files: ['**/*.svelte'],
		languageOptions: {
			parserOptions: { parser: ts.parser }
		}
	},
	{
		rules: {
			// TypeScript already reports unknown identifiers (and knows the DOM lib
			// types like ShareData that this rule does not).
			'no-undef': 'off',
			'@typescript-eslint/no-unused-vars': [
				'error',
				{ argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' }
			]
		}
	},
	// src/lib/paraglide/ is generated (gitignored) — linting it produced ~1,800 warnings and 8x the runtime.
	{
		// `...rest` on $props() only matters for custom elements, which this app
		// never compiles; the compiler hint is the only warning the file has.
		files: ['src/lib/components/ui/Button.svelte'],
		rules: { 'svelte/valid-compile': ['error', { ignoreWarnings: true }] }
	},
	{
		// The legal pages render hand-authored TS content (lib/legal/*/content.ts),
		// never user input; every other {@html} site carries its own justification.
		files: ['src/lib/legal/LegalDocument.svelte'],
		rules: { 'svelte/no-at-html-tags': 'off' }
	},
	{ ignores: ['build/', '.svelte-kit/', 'node_modules/', 'src/lib/paraglide/'] }
);
