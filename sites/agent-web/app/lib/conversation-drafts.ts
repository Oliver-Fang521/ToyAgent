export type ConversationDraft = { question: string; file: File | null };
export type ConversationDrafts = ReadonlyMap<string | null, ConversationDraft>;
export const emptyDraft: ConversationDraft = { question: "", file: null };

export function writeDraft(drafts: ConversationDrafts, id: string | null, draft: ConversationDraft): ConversationDrafts {
  const next = new Map(drafts);
  if (draft.question || draft.file) next.set(id, draft);
  else next.delete(id);
  return next;
}

/** Give a newly created conversation its composer before attempting the first upload. */
export function claimNewDraft(drafts: ConversationDrafts, id: string): ConversationDrafts {
  const draft = drafts.get(null);
  if (!draft) return drafts;
  const next = new Map(drafts);
  next.delete(null);
  next.set(id, draft);
  return next;
}
