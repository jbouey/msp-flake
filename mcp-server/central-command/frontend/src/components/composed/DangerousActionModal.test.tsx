import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DangerousActionModal } from './DangerousActionModal';

/**
 * DangerousActionModal unit tests — Sprint-N+1 Decision 2 (2026-05-08).
 *
 * Covers both tiers + a11y:
 *   1.  open=false renders nothing
 *   2.  tier-1 disables submit until the typed value matches the target
 *   3.  tier-1 with confirmInput="LITERAL" requires that literal exactly
 *   4.  tier-2 enables submit immediately
 *   5.  ESC closes via onCancel
 *   6.  ENTER submits when matched (tier-1)
 *   7.  ENTER does NOT submit when not matched
 *   8.  busy=true disables both buttons + the input + shows spinner state
 *   9.  errorMessage renders in an alert region
 *  10.  role=dialog + aria-modal + aria-labelledby + aria-describedby
 *  11.  Tab cycles forward; Shift+Tab cycles backward (focus trap)
 *  12.  match indicator uses aria-live=polite and toggles message
 *  13.  Tier-2: ENTER on the cancel button does NOT confirm
 */

describe('DangerousActionModal', () => {
  it('renders nothing when open=false', () => {
    const { container } = render(
      <DangerousActionModal
        open={false}
        tier="reversible"
        title="X"
        verb="X"
        target="x"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('tier-1 keeps confirm disabled until typed value matches target', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <DangerousActionModal
        open
        tier="irreversible"
        title="Delete user"
        verb="Delete"
        target="alice@example.com"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );
    const submit = screen.getByRole('button', { name: /^Delete$/ });
    expect(submit).toBeDisabled();
    const input = screen.getByTestId('dangerous-action-confirm-input');
    await user.type(input, 'alice@example.com');
    expect(submit).not.toBeDisabled();
    await user.click(submit);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('tier-1 with explicit confirmInput literal requires that exact literal', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const { container } = render(
      <DangerousActionModal
        open
        tier="irreversible"
        title="Cancel subscription"
        verb="Cancel"
        target="Professional plan"
        confirmInput="CANCEL"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );
    // verb collides with the Cancel button label, so target the confirm
    // button by its data-attribute rather than accessible name.
    const submit = container.querySelector(
      'button[data-dangerous-modal-role="confirm"]',
    ) as HTMLButtonElement;
    expect(submit).toBeDisabled();
    const input = screen.getByTestId('dangerous-action-confirm-input');
    await user.type(input, 'Professional plan');
    expect(submit).toBeDisabled();
    await user.clear(input);
    await user.type(input, 'CANCEL');
    expect(submit).not.toBeDisabled();
  });

  it('tier-2 enables submit immediately (no typed gate)', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <DangerousActionModal
        open
        tier="reversible"
        title="Invite user"
        verb="Invite"
        target="bob@example.com"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('dangerous-action-confirm-input')).toBeNull();
    const submit = screen.getByRole('button', { name: /^Invite$/ });
    expect(submit).not.toBeDisabled();
    await user.click(submit);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('ESC calls onCancel', () => {
    const onCancel = vi.fn();
    render(
      <DangerousActionModal
        open
        tier="reversible"
        title="X"
        verb="X"
        target="x"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );
    const dialog = screen.getByRole('dialog');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('ENTER submits when tier-1 typed input matches', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <DangerousActionModal
        open
        tier="irreversible"
        title="Delete"
        verb="Delete"
        target="x"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );
    const input = screen.getByTestId('dangerous-action-confirm-input');
    await user.type(input, 'x');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('ENTER does NOT submit when tier-1 input does not match', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <DangerousActionModal
        open
        tier="irreversible"
        title="Delete"
        verb="Delete"
        target="alice"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );
    const input = screen.getByTestId('dangerous-action-confirm-input');
    await user.type(input, 'al');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('busy=true disables both buttons and the input, shows spinner state', () => {
    render(
      <DangerousActionModal
        open
        tier="irreversible"
        title="Delete"
        verb="Delete"
        target="x"
        busy={true}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const cancel = screen.getByRole('button', { name: /^Cancel$/ });
    expect(cancel).toBeDisabled();
    // While busy, the submit button shows the verb with an ellipsis.
    const busySubmit = screen.getByRole('button', { name: /Delete…/ });
    expect(busySubmit).toBeDisabled();
    const input = screen.getByTestId('dangerous-action-confirm-input');
    expect(input).toBeDisabled();
  });

  it('errorMessage renders in an alert region', () => {
    render(
      <DangerousActionModal
        open
        tier="reversible"
        title="X"
        verb="X"
        target="x"
        errorMessage="Something went wrong."
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Something went wrong.');
  });

  it('dialog has the expected a11y semantics', () => {
    render(
      <DangerousActionModal
        open
        tier="irreversible"
        title="Delete user"
        verb="Delete"
        target="alice"
        description="This cannot be undone."
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAttribute('aria-labelledby');
    expect(dialog).toHaveAttribute('aria-describedby');
    const titleId = dialog.getAttribute('aria-labelledby');
    const descId = dialog.getAttribute('aria-describedby');
    expect(document.getElementById(titleId!)).toHaveTextContent('Delete user');
    expect(document.getElementById(descId!)).toHaveTextContent(
      'This cannot be undone.',
    );
  });

  it('Tab cycles within the dialog (focus trap, forward + backward)', async () => {
    const user = userEvent.setup();
    render(
      <>
        <button data-testid="outside-before">outside-before</button>
        <DangerousActionModal
          open
          tier="reversible"
          title="X"
          verb="X"
          target="x"
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
        />
        <button data-testid="outside-after">outside-after</button>
      </>,
    );
    // Cancel + Confirm are the two focusable elements in tier-2.
    const cancel = screen.getByRole('button', { name: /^Cancel$/ });
    const confirm = screen.getByRole('button', { name: /^X$/ });
    cancel.focus();
    expect(document.activeElement).toBe(cancel);
    await user.tab();
    expect(document.activeElement).toBe(confirm);
    // Tab from last → wraps to first.
    await user.tab();
    expect(document.activeElement).toBe(cancel);
    // Shift+Tab from first → wraps to last.
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(confirm);
  });

  it('match indicator toggles via aria-live region', async () => {
    const user = userEvent.setup();
    render(
      <DangerousActionModal
        open
        tier="irreversible"
        title="Delete"
        verb="Delete"
        target="alice"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const input = screen.getByTestId('dangerous-action-confirm-input');
    await user.type(input, 'al');
    expect(screen.getByText(/does not yet match/i)).toBeInTheDocument();
    await user.type(input, 'ice');
    expect(screen.getByText(/Typed value matches/i)).toBeInTheDocument();
  });

  it('tier-2: ENTER while focused on Cancel does NOT submit', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <DangerousActionModal
        open
        tier="reversible"
        title="X"
        verb="X"
        target="x"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    const cancel = screen.getByRole('button', { name: /^Cancel$/ });
    cancel.focus();
    fireEvent.keyDown(cancel, { key: 'Enter' });
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('clicking the backdrop calls onCancel (when not busy)', () => {
    const onCancel = vi.fn();
    render(
      <DangerousActionModal
        open
        tier="reversible"
        title="X"
        verb="X"
        target="x"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );
    const dialog = screen.getByRole('dialog');
    const backdrop = dialog.parentElement!;
    fireEvent.click(backdrop);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('clicking the backdrop while busy does NOT call onCancel', () => {
    const onCancel = vi.fn();
    render(
      <DangerousActionModal
        open
        tier="reversible"
        title="X"
        verb="X"
        target="x"
        busy={true}
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );
    const dialog = screen.getByRole('dialog');
    const backdrop = dialog.parentElement!;
    fireEvent.click(backdrop);
    expect(onCancel).not.toHaveBeenCalled();
  });
});
