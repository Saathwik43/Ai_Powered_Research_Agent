/** Helpers for the saved lists (PDF chats, surveys, drafts) — ROW-1 / ROW-2.
    Kept out of SavedItemActions.jsx so that file exports only components. */

/** Split a server-ordered list (pinned first) into its two runs. */
export function splitPinned(items) {
  const pinned = [];
  const rest = [];
  for (const item of items || []) (item?.pinned ? pinned : rest).push(item);
  return { pinned, rest };
}

/**
 * Re-sort after an optimistic pin, in the server's order (ROW-1): pinned
 * first, then newest. Ids are ObjectId hex, so string order is creation order.
 */
export function orderPinned(items, idKey = 'id') {
  return [...items].sort((a, b) =>
    (b.pinned === true) - (a.pinned === true) || (a[idKey] < b[idKey] ? 1 : -1));
}

/** The name a saved row shows: its rename, else its original key. */
export function displayName(item, fallbackKey) {
  const title = (item?.title || '').trim();
  return title || (item?.[fallbackKey] || '').trim();
}
