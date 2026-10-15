import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import SplitResizer from './SplitResizer';

describe('SplitResizer component', () => {
  it('renders resizer handle and snap preset buttons', () => {
    const onSplitChange = vi.fn();
    render(<SplitResizer splitRatio={50} onSplitChange={onSplitChange} />);

    expect(screen.getByTestId('split-resizer')).toBeInTheDocument();
    expect(screen.getByTestId('split-resizer-handle')).toHaveTextContent('⋮');
    expect(screen.getByTestId('split-snap-30')).toBeInTheDocument();
    expect(screen.getByTestId('split-snap-50')).toBeInTheDocument();
    expect(screen.getByTestId('split-snap-70')).toBeInTheDocument();
  });

  it('triggers onSplitChange with snap presets when clicked', () => {
    const onSplitChange = vi.fn();
    render(<SplitResizer splitRatio={50} onSplitChange={onSplitChange} />);

    fireEvent.click(screen.getByTestId('split-snap-30'));
    expect(onSplitChange).toHaveBeenCalledWith(30);

    fireEvent.click(screen.getByTestId('split-snap-70'));
    expect(onSplitChange).toHaveBeenCalledWith(70);

    fireEvent.click(screen.getByTestId('split-snap-50'));
    expect(onSplitChange).toHaveBeenCalledWith(50);
  });

  it('resets to 50/50 on double click', () => {
    const onSplitChange = vi.fn();
    render(<SplitResizer splitRatio={30} onSplitChange={onSplitChange} />);

    fireEvent.doubleClick(screen.getByTestId('split-resizer'));
    expect(onSplitChange).toHaveBeenCalledWith(50);
  });
});
