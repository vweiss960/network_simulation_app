/** User-facing message for a failed beginEditSession() call (the API error
 * message carries the HTTP status). */
export function editSessionErrorMessage(err: any): string {
  const msg = err?.message || '';
  return msg.includes('409') ? 'Locked by another user'
    : msg.includes('403') ? 'Only an administrator can edit this topology'
    : 'Could not enter edit mode';
}
