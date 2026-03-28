import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { GlassCard } from './shared/GlassCard';
import { Badge, HealthBadge, LevelBadge, SeverityBadge } from './shared/Badge';
import { Spinner, LoadingScreen, LoadingInline } from './shared/Spinner';

describe('GlassCard', () => {
  it('renders children', () => {
    render(<GlassCard><p>Hello World</p></GlassCard>);
    expect(screen.getByText('Hello World')).toBeInTheDocument();
  });

  it('applies glass-card base class', () => {
    const { container } = render(<GlassCard>Content</GlassCard>);
    const card = container.firstElementChild!;
    expect(card.className).toContain('glass-card');
  });

  it('applies custom className', () => {
    const { container } = render(<GlassCard className="my-custom">Content</GlassCard>);
    const card = container.firstElementChild!;
    expect(card.className).toContain('my-custom');
  });

  it('applies hover classes when hover prop is true', () => {
    const { container } = render(<GlassCard hover>Content</GlassCard>);
    const card = container.firstElementChild!;
    expect(card.className).toContain('cursor-pointer');
    expect(card.className).toContain('hover:shadow-card-hover');
  });

  it('does not apply hover classes by default', () => {
    const { container } = render(<GlassCard>Content</GlassCard>);
    const card = container.firstElementChild!;
    expect(card.className).not.toContain('cursor-pointer');
  });

  it('sets role="button" when onClick is provided', () => {
    render(<GlassCard onClick={() => {}}>Clickable</GlassCard>);
    expect(screen.getByRole('button')).toBeInTheDocument();
  });

  it('does not set role when onClick is absent', () => {
    const { container } = render(<GlassCard>Not clickable</GlassCard>);
    expect(container.querySelector('[role="button"]')).toBeNull();
  });

  it('calls onClick when clicked', async () => {
    const user = userEvent.setup();
    let clicked = false;
    render(<GlassCard onClick={() => { clicked = true; }}>Click me</GlassCard>);
    await user.click(screen.getByText('Click me'));
    expect(clicked).toBe(true);
  });

  it('applies correct padding classes', () => {
    const { container: c1 } = render(<GlassCard padding="none">A</GlassCard>);
    expect(c1.firstElementChild!.className).not.toContain('p-');

    const { container: c2 } = render(<GlassCard padding="sm">B</GlassCard>);
    expect(c2.firstElementChild!.className).toContain('p-3');

    const { container: c3 } = render(<GlassCard padding="md">C</GlassCard>);
    expect(c3.firstElementChild!.className).toContain('p-4');

    const { container: c4 } = render(<GlassCard padding="lg">D</GlassCard>);
    expect(c4.firstElementChild!.className).toContain('p-6');
  });
});

describe('Badge', () => {
  it('renders children text', () => {
    render(<Badge>Test Label</Badge>);
    expect(screen.getByText('Test Label')).toBeInTheDocument();
  });

  it('renders as a span with rounded-full class', () => {
    const { container } = render(<Badge>Label</Badge>);
    const span = container.firstElementChild!;
    expect(span.tagName).toBe('SPAN');
    expect(span.className).toContain('rounded-full');
  });

  it('applies default variant classes', () => {
    const { container } = render(<Badge>Default</Badge>);
    const span = container.firstElementChild!;
    expect(span.className).toContain('bg-fill-secondary');
    expect(span.className).toContain('text-label-secondary');
  });

  it('applies success variant classes', () => {
    const { container } = render(<Badge variant="success">OK</Badge>);
    const span = container.firstElementChild!;
    expect(span.className).toContain('bg-health-healthy/15');
  });

  it('applies error variant classes', () => {
    const { container } = render(<Badge variant="error">Fail</Badge>);
    const span = container.firstElementChild!;
    expect(span.className).toContain('bg-health-critical/15');
  });

  it('applies warning variant classes', () => {
    const { container } = render(<Badge variant="warning">Warn</Badge>);
    const span = container.firstElementChild!;
    expect(span.className).toContain('bg-health-warning/15');
  });

  it('applies info variant classes', () => {
    const { container } = render(<Badge variant="info">Info</Badge>);
    const span = container.firstElementChild!;
    expect(span.className).toContain('bg-ios-blue/15');
  });

  it('applies custom className', () => {
    const { container } = render(<Badge className="extra">X</Badge>);
    expect(container.firstElementChild!.className).toContain('extra');
  });
});

describe('HealthBadge', () => {
  it('renders "Healthy" for healthy status', () => {
    render(<HealthBadge status="healthy" />);
    expect(screen.getByText('Healthy')).toBeInTheDocument();
  });

  it('renders "Critical" for critical status', () => {
    render(<HealthBadge status="critical" />);
    expect(screen.getByText('Critical')).toBeInTheDocument();
  });

  it('renders "Warning" for warning status', () => {
    render(<HealthBadge status="warning" />);
    expect(screen.getByText('Warning')).toBeInTheDocument();
  });
});

describe('LevelBadge', () => {
  it('renders level text', () => {
    render(<LevelBadge level="L1" />);
    expect(screen.getByText('L1')).toBeInTheDocument();
  });

  it('shows label when showLabel is true', () => {
    render(<LevelBadge level="L2" showLabel />);
    expect(screen.getByText('L2 LLM')).toBeInTheDocument();
  });
});

describe('SeverityBadge', () => {
  it('renders severity text', () => {
    render(<SeverityBadge severity="high" />);
    expect(screen.getByText('high')).toBeInTheDocument();
  });

  it('applies uppercase class', () => {
    const { container } = render(<SeverityBadge severity="low" />);
    expect(container.firstElementChild!.className).toContain('uppercase');
  });
});

describe('Spinner', () => {
  it('renders an SVG with role="status"', () => {
    render(<Spinner />);
    const svg = screen.getByRole('status');
    expect(svg).toBeInTheDocument();
    expect(svg.tagName.toLowerCase()).toBe('svg');
  });

  it('has aria-label "Loading"', () => {
    render(<Spinner />);
    expect(screen.getByLabelText('Loading')).toBeInTheDocument();
  });

  it('applies size classes', () => {
    const { container: c1 } = render(<Spinner size="sm" />);
    expect(c1.firstElementChild!.getAttribute('class')).toContain('h-4');

    const { container: c2 } = render(<Spinner size="lg" />);
    expect(c2.firstElementChild!.getAttribute('class')).toContain('h-8');
  });

  it('applies custom color', () => {
    const { container } = render(<Spinner color="text-red-500" />);
    expect(container.firstElementChild!.getAttribute('class')).toContain('text-red-500');
  });
});

describe('LoadingScreen', () => {
  it('renders default "Loading..." message', () => {
    render(<LoadingScreen />);
    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  it('renders custom message', () => {
    render(<LoadingScreen message="Please wait" />);
    expect(screen.getByText('Please wait')).toBeInTheDocument();
  });

  it('includes a Spinner', () => {
    render(<LoadingScreen />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
});

describe('LoadingInline', () => {
  it('renders spinner without message', () => {
    render(<LoadingInline />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('renders spinner with message text', () => {
    render(<LoadingInline message="Saving..." />);
    expect(screen.getByText('Saving...')).toBeInTheDocument();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
});
