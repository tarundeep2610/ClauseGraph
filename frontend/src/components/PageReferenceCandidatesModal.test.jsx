import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import PageReferenceCandidatesModal from './PageReferenceCandidatesModal';

describe('PageReferenceCandidatesModal', () => {
  it('renders nothing when target is null', () => {
    render(<PageReferenceCandidatesModal target={null} onClose={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.queryByTestId('page-reference-candidates-modal')).not.toBeInTheDocument();
  });

  it('shows the mention as the title and one row per candidate across both groups', () => {
    const target = {
      mentionRaw: 'para 1',
      exact: [{ page: 8, scope_label: 'Affidavit of Ishwar Chand Balan' }],
      approximate: [{ page: 9, own_label: 'Respectfully Showeth' }, { page: 11 }],
    };
    render(<PageReferenceCandidatesModal target={target} onClose={vi.fn()} onSelect={vi.fn()} />);
    const modal = screen.getByTestId('page-reference-candidates-modal');
    expect(modal).toHaveTextContent('para 1');
    expect(modal).toHaveTextContent('Exact Match');
    expect(modal).toHaveTextContent('Possible');
    expect(modal).toHaveTextContent('Page 8');
    expect(modal).toHaveTextContent('Affidavit of Ishwar Chand Balan');
    expect(modal).toHaveTextContent('Page 9');
    expect(modal).toHaveTextContent('Page 11');
  });

  it('omits a group entirely when it has no candidates', () => {
    const target = { mentionRaw: 'clause 9', exact: [], approximate: [{ page: 25 }] };
    render(<PageReferenceCandidatesModal target={target} onClose={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.queryByText('Exact Match')).not.toBeInTheDocument();
    expect(screen.getByText(/Possible/)).toBeInTheDocument();
  });

  it('a row click calls onSelect with that exact candidate', () => {
    const onSelect = vi.fn();
    const candidate = { page: 61, anchor_id: 'a1' };
    const target = { mentionRaw: 'Annexure A-1', exact: [candidate], approximate: [] };
    render(<PageReferenceCandidatesModal target={target} onClose={vi.fn()} onSelect={onSelect} />);
    fireEvent.click(screen.getByText('Page 61'));
    expect(onSelect).toHaveBeenCalledWith(candidate);
  });

  it('Escape and backdrop click call onClose without calling onSelect; clicking inside the panel does not close it', () => {
    const onClose = vi.fn();
    const onSelect = vi.fn();
    const target = { mentionRaw: 'para 1', exact: [{ page: 8 }], approximate: [] };
    render(<PageReferenceCandidatesModal target={target} onClose={onClose} onSelect={onSelect} />);

    fireEvent.click(screen.getByTestId('page-reference-candidates-modal'));
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId('page-reference-candidates-modal-backdrop'));
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(onSelect).not.toHaveBeenCalled();
  });
});
