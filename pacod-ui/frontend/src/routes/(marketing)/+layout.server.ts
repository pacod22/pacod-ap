import type { LayoutServerLoad } from './$types';

/** The public info & legal pages. */
export const load: LayoutServerLoad = async ({ setHeaders }) => {
	// Hand-authored content; let a CDN / the edge keep it for a few minutes
	// (`Vary: Cookie` from the hooks keeps a signed-in render out of the shared
	// copy for anonymous visitors).
	setHeaders({ 'cache-control': 'public, max-age=0, s-maxage=600, stale-while-revalidate=60' });
};
