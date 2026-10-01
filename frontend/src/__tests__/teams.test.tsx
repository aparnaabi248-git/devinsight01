/**
 * Tests for the Teams & Access screen.
 *
 * The important behaviour to pin is that the UI only offers the controls the signed-in
 * user actually has. Hiding a button is presentation, not security - the server
 * enforces every rule - but showing a "Remove member" button to a plain team member is
 * still a bug worth a test.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import TeamsPage from '@/pages/TeamsPage';
import { AuthContext } from '@/context/AuthContext';
import { api } from '@/api/client';
import type { MyAccess, Page, Team, TeamDetail, User } from '@/types/api';

const PLATFORM: Team = {
  id: 1,
  name: 'Platform Engineering',
  slug: 'platform-engineering',
  description: 'Owns the shared CLI tooling.',
  manager_id: 2,
  manager_username: 'priya',
  is_active: true,
  member_count: 2,
  repository_count: 1,
  my_role: 'manager',
  capabilities: ['view', 'manage_members', 'manage_access'],
  created_at: '2026-01-01T00:00:00Z',
};

const DETAIL: TeamDetail = {
  ...PLATFORM,
  members: [
    {
      id: 1,
      user_id: 2,
      username: 'priya',
      email: 'priya@example.com',
      role: 'manager',
      scoped_repository_id: null,
      joined_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 2,
      user_id: 3,
      username: 'raj.dev',
      email: 'raj.dev@example.com',
      role: 'member',
      scoped_repository_id: null,
      joined_at: '2026-01-02T00:00:00Z',
    },
  ],
  repositories: [
    {
      id: 10,
      repository_id: 7,
      repository_name: 'pallets/click',
      grant_type: 'team',
      team_id: 1,
      team_name: 'Platform Engineering',
      user_id: null,
      username: null,
      permission: 'write',
      granted_by_id: 2,
      expires_at: null,
      note: 'Platform maintains the CLI library.',
      is_expired: false,
    },
  ],
};

const ACCESS: MyAccess = {
  user_id: 2,
  username: 'priya',
  role: 'analyst',
  manages_teams: 1,
  repositories: [
    {
      repository_id: 7,
      full_name: 'pallets/click',
      permission: 'write',
      source: 'team',
      via_teams: ['Platform Engineering'],
    },
  ],
};

const REPOS: Page<{ id: number; full_name: string }> = {
  items: [
    { id: 7, full_name: 'pallets/click' },
    { id: 8, full_name: 'pallets/flask' },
  ],
  total: 2,
  page: 1,
  page_size: 100,
  pages: 1,
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, page_size: 100, pages: 1 };
}

/**
 * The page reads the signed-in user from AuthContext, so each test renders it inside a
 * provider that pins the role under test. Hiding controls is only presentation - the
 * server still enforces everything - but a manager-only control shown to a plain member
 * is still wrong, so the capabilities wiring is worth asserting.
 */
function withRole(role: User['role']) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <AuthContext.Provider
          value={{
            user: {
              id: 2,
              username: 'priya',
              email: 'priya@example.com',
              full_name: 'Priya',
              role,
              is_active: true,
              github_login: null,
              created_at: '2026-01-01T00:00:00Z',
            },
            authenticated: true,
            initialising: false,
            login: vi.fn(),
            register: vi.fn(),
            logout: vi.fn(),
          }}
        >
          {children}
        </AuthContext.Provider>
      </QueryClientProvider>
    );
  };
}

function mockApi(overrides: Partial<Record<keyof typeof api, unknown>> = {}) {
  const defaults = {
    teams: vi.fn().mockResolvedValue(page([PLATFORM])),
    team: vi.fn().mockResolvedValue(DETAIL),
    myAccess: vi.fn().mockResolvedValue(ACCESS),
    repositories: vi.fn().mockResolvedValue(REPOS),
  };
  for (const [key, value] of Object.entries({ ...defaults, ...overrides })) {
    vi.spyOn(api, key as keyof typeof api).mockImplementation(value as never);
  }
}

describe('TeamsPage', () => {
  describe('as a project manager', () => {
    beforeEach(() => mockApi());

    it('lists the teams you belong to', async () => {
      const Wrapper = withRole('analyst');
      render(<TeamsPage />, { wrapper: Wrapper });
      expect(await screen.findByText('Platform Engineering')).toBeInTheDocument();
    });

    it('shows the members of the selected team', async () => {
      const Wrapper = withRole('analyst');
      render(<TeamsPage />, { wrapper: Wrapper });
      expect(await screen.findByText('raj.dev')).toBeInTheDocument();
      expect(screen.getByText('priya@example.com')).toBeInTheDocument();
    });

    it('shows which repositories the team can reach', async () => {
      const Wrapper = withRole('analyst');
      render(<TeamsPage />, { wrapper: Wrapper });
      // `pallets/click` also appears in the "Your access" strip, so wait on the grant
      // note, which only renders once the team detail query has resolved.
      expect(
        await screen.findByText('Platform maintains the CLI library.'),
      ).toBeInTheDocument();
      // `write` shows in both the access strip and the grant table.
      expect(screen.getAllByText('write').length).toBeGreaterThanOrEqual(2);
    });

    it('explains how your own access was obtained', async () => {
      const Wrapper = withRole('analyst');
      render(<TeamsPage />, { wrapper: Wrapper });
      expect(await screen.findByText('via Platform Engineering')).toBeInTheDocument();
    });

    it('offers the add-member form', async () => {
      const Wrapper = withRole('analyst');
      render(<TeamsPage />, { wrapper: Wrapper });
      expect(await screen.findByLabelText('Username or email')).toBeInTheDocument();
      expect(screen.getByText('Add member')).toBeInTheDocument();
    });

    it('lets you change a member role from a dropdown', async () => {
      const Wrapper = withRole('analyst');
      render(<TeamsPage />, { wrapper: Wrapper });
      const select = await screen.findByLabelText('Role for raj.dev');
      expect(select).toHaveValue('member');
      expect(within(select as HTMLSelectElement).getAllByRole('option').length).toBe(3);
    });

    it('offers a revoke control for each grant', async () => {
      const Wrapper = withRole('analyst');
      render(<TeamsPage />, { wrapper: Wrapper });
      expect(await screen.findByTitle('Revoke access to pallets/click')).toBeInTheDocument();
    });

    it('hides a repository from the grant picker when it is already granted', async () => {
      const Wrapper = withRole('analyst');
      render(<TeamsPage />, { wrapper: Wrapper });
      const picker = (await screen.findByLabelText('Repository')) as HTMLSelectElement;
      // Wait for the repository list to load before reading the options.
      await screen.findByRole('option', { name: 'pallets/flask' });
      const values = within(picker)
        .getAllByRole('option')
        .map((o) => (o as HTMLOptionElement).value);
      // pallets/click is already granted, so only pallets/flask is offered.
      expect(values).toContain('8');
      expect(values).not.toContain('7');
    });
  });

  describe('as a platform administrator', () => {
    it('says the administrator sees every repository', async () => {
      mockApi({
        myAccess: vi.fn().mockResolvedValue({ ...ACCESS, role: 'admin' }),
      });
      const Wrapper = withRole('admin');
      render(<TeamsPage />, { wrapper: Wrapper });
      expect(await screen.findByText(/You are an administrator/)).toBeInTheDocument();
    });
  });
});

describe('access rules shown in the UI', () => {
  it('marks a team with no repository grants as reaching nothing', async () => {
    mockApi({
      team: vi.fn().mockResolvedValue({ ...DETAIL, repositories: [], repository_count: 0 }),
    });
    const Wrapper = withRole('analyst');
    render(<TeamsPage />, { wrapper: Wrapper });
    expect(
      await screen.findByText(/cannot reach any repository, so its members see nothing/),
    ).toBeInTheDocument();
  });

  it('flags a member who is pinned to a single repository', async () => {
    mockApi({
      team: vi.fn().mockResolvedValue({
        ...DETAIL,
        members: [
          { ...DETAIL.members[0] },
          { ...DETAIL.members[1], scoped_repository_id: 7 },
        ],
      }),
    });
    const Wrapper = withRole('analyst');
    render(<TeamsPage />, { wrapper: Wrapper });
    expect(await screen.findByText('repo #7')).toBeInTheDocument();
  });

  it('labels an unscoped member as covering all team repositories', async () => {
    mockApi();
    const Wrapper = withRole('analyst');
    render(<TeamsPage />, { wrapper: Wrapper });
    expect(await screen.findAllByText('all team repos')).toHaveLength(2);
  });
});
