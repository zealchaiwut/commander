/* Project switcher — keyboard-accessible, all-viewport (issue #2291).
 *
 * Exports:
 *   buildProjectSwitcherOptions(projects, currentSlug) — pure; generates <option> HTML
 *   navigateToProject(slug, currentSlug, currentTab)   — navigate preserving active tab
 */
/* global escHtml */

/**
 * Build the <option> elements for the project switcher select.
 *
 * @param {Array<{slug:string,name:string}>} projects
 * @param {string} currentSlug
 * @returns {string} HTML for the <option> elements
 */
export function buildProjectSwitcherOptions(projects, currentSlug) {
  if (!projects || !projects.length) return '';
  return projects
    .map((proj) => {
      const slug = proj.slug || '';
      const name = proj.name || slug;
      const selected = slug === currentSlug ? ' selected' : '';
      return `<option value="${escHtml(slug)}"${selected}>${escHtml(name)}</option>`;
    })
    .join('');
}

/**
 * Navigate to a project, preserving the current tab where it exists.
 * Falls back to 'sprint-mgmt' when currentTab is absent.
 *
 * @param {string} slug        - target project slug
 * @param {string} currentSlug - slug of the currently-viewed project
 * @param {string} [currentTab]- active tab name (e.g. 'failures', 'tickets')
 */
export function navigateToProject(slug, currentSlug, currentTab) {
  if (!slug || slug === currentSlug) return;
  const tab = currentTab || 'sprint-mgmt';
  window.location.href = '/project/' + encodeURIComponent(slug) + '/' + tab;
}
