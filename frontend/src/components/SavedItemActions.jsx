import { useEffect, useRef, useState } from 'react';
import { MoreHorizontal, Pencil, Pin, PinOff, Trash2 } from 'lucide-react';
import { Button } from './ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from './ui/dropdown-menu';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from './ui/alert-dialog';

/**
 * Row actions for a saved PDF chat, survey or draft (ROW-2) — the ChatGPT /
 * Claude pattern, one copy for every list:
 *
 *   ⋯ → Pin / Unpin · Rename · Delete
 *
 * Delete always asks first, in an AlertDialog, and keeps the dialog open with
 * the error if the request fails. A × never deletes anything in this app; it
 * closes or removes from an input.
 *
 * The trigger rests dimmed, not hidden — the app's rule (see Dashboard.css):
 * a control that only exists on hover is unreachable on touch and
 * undiscoverable elsewhere. It goes full strength when its row is hovered or
 * holds focus (the row must carry Tailwind's `group` class), while its menu
 * is open, and always on touch screens.
 *
 * `onDelete` returns a promise and should throw with a user-facing message.
 */
export function SavedItemMenu({
  name,
  kind = 'item',
  pinned = false,
  onTogglePin,
  onRename,
  onDelete,
  deleteDetail,
  className = '',
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Rename starts only once the menu has closed: while it is open its focus
  // trap pulls focus back out of the new field, and on close it would hand
  // focus to the trigger — blurring the field, and blur means "done".
  const renamePendingRef = useRef(false);

  const runDelete = async () => {
    setBusy(true);
    setError('');
    try {
      await onDelete();
      setConfirming(false);
    } catch (e) {
      setError(e?.message || `Could not delete this ${kind}.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={`More actions for ${name}`}
            title="More actions"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
            className={`shrink-0 text-muted-foreground opacity-40 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100 [@media(hover:none)]:opacity-100 ${className}`}
          >
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="w-auto min-w-36"
          onClick={(e) => e.stopPropagation()}
          onCloseAutoFocus={(e) => {
            if (!renamePendingRef.current) return;
            renamePendingRef.current = false;
            e.preventDefault();
            onRename();
          }}
        >
          {onTogglePin && (
            <DropdownMenuItem onSelect={onTogglePin}>
              {pinned ? <PinOff /> : <Pin />}
              {pinned ? 'Unpin' : 'Pin'}
            </DropdownMenuItem>
          )}
          {onRename && (
            <DropdownMenuItem onSelect={() => { renamePendingRef.current = true; }}>
              <Pencil />
              Rename
            </DropdownMenuItem>
          )}
          {onDelete && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                onSelect={() => { setError(''); setConfirming(true); }}
              >
                <Trash2 />
                Delete
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      {onDelete && (
        <AlertDialog open={confirming} onOpenChange={(open) => { if (!busy) setConfirming(open); }}>
          <AlertDialogContent onClick={(e) => e.stopPropagation()}>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete this {kind}?</AlertDialogTitle>
              <AlertDialogDescription>
                “{name}”{deleteDetail ? ` ${deleteDetail}` : ' will be removed.'} This cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            {/* A div: the global unlayered `p { color }` beats text-destructive. */}
            {error && <div role="alert" className="text-sm text-destructive">{error}</div>}
            <AlertDialogFooter>
              <AlertDialogCancel disabled={busy}>Keep it</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                disabled={busy}
                onClick={(e) => { e.preventDefault(); runDelete(); }}
              >
                {busy ? 'Deleting…' : `Delete ${kind}`}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </>
  );
}

/**
 * Inline rename: replaces the row's name while editing. Enter or leaving the
 * field saves, Escape cancels. An empty name restores the original one.
 */
export function RenameField({ initial, label, onSubmit, onCancel, className = '' }) {
  const [value, setValue] = useState(initial || '');
  const ref = useRef(null);
  const doneRef = useRef(false);

  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  const finish = (save) => {
    if (doneRef.current) return;
    doneRef.current = true;
    if (save && value.trim() !== (initial || '').trim()) onSubmit(value);
    else onCancel();
  };

  return (
    <input
      ref={ref}
      className={`min-w-0 flex-1 ${className}`}
      value={value}
      maxLength={200}
      aria-label={label || 'New name'}
      onChange={(e) => setValue(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === 'Enter') { e.preventDefault(); finish(true); }
        if (e.key === 'Escape') { e.preventDefault(); finish(false); }
      }}
      onBlur={() => finish(true)}
    />
  );
}
