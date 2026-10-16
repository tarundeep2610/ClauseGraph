import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import StudioRail from './StudioRail';

describe('StudioRail component', () => {
  it('renders outline and review buttons and triggers callbacks on click', () => {
    const onToggleStructure = vi.fn();
    const onToggleReview = vi.fn();

    render(
      <StudioRail
        structureOpen={false}
        onToggleStructure={onToggleStructure}
        reviewOpen={false}
        onToggleReview={onToggleReview}
        reviewCount={0}
      />
    );

    const outlineBtn = screen.getByTestId('structure-sidebar-toggle-btn');
    const reviewBtn = screen.getByTestId('review-sidebar-toggle-btn');

    expect(outlineBtn).toBeInTheDocument();
    expect(reviewBtn).toBeInTheDocument();
    expect(screen.queryByTestId('studio-rail-review-badge')).not.toBeInTheDocument();

    fireEvent.click(outlineBtn);
    expect(onToggleStructure).toHaveBeenCalledTimes(1);

    fireEvent.click(reviewBtn);
    expect(onToggleReview).toHaveBeenCalledTimes(1);
  });

  it('renders alert badge and active state when reviewCount > 0 and drawers are open', () => {
    render(
      <StudioRail
        structureOpen={true}
        onToggleStructure={vi.fn()}
        reviewOpen={true}
        onToggleReview={vi.fn()}
        reviewCount={4}
      />
    );

    expect(screen.getByTestId('structure-sidebar-toggle-btn')).toHaveClass('active');
    expect(screen.getByTestId('review-sidebar-toggle-btn')).toHaveClass('active');
    expect(screen.getByTestId('studio-rail-review-badge')).toHaveTextContent('4');
  });

  it('shows the Outline button as visually disabled when indexBuilt=false, but still calls onToggleStructure on click (the toast-on-click, not a silent no-op)', () => {
    const onToggleStructure = vi.fn();
    render(
      <StudioRail
        structureOpen={false}
        onToggleStructure={onToggleStructure}
        indexBuilt={false}
        reviewOpen={false}
        onToggleReview={vi.fn()}
        reviewCount={0}
      />
    );

    const outlineBtn = screen.getByTestId('structure-sidebar-toggle-btn');
    expect(outlineBtn).toHaveClass('disabled');
    expect(outlineBtn).toHaveAttribute('aria-disabled', 'true');

    fireEvent.click(outlineBtn);
    expect(onToggleStructure).toHaveBeenCalledTimes(1);
  });

  it('responds to global Ctrl+1 and Ctrl+2 keyboard shortcuts', () => {
    const onToggleStructure = vi.fn();
    const onToggleReview = vi.fn();

    render(
      <StudioRail
        structureOpen={false}
        onToggleStructure={onToggleStructure}
        reviewOpen={false}
        onToggleReview={onToggleReview}
        reviewCount={0}
      />
    );

    fireEvent.keyDown(window, { key: '1', ctrlKey: true });
    expect(onToggleStructure).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: '2', ctrlKey: true });
    expect(onToggleReview).toHaveBeenCalledTimes(1);
  });
});
