/**
 * Team & access management.
 *
 * This is the project manager's screen: create teams, add and remove members, set each
 * member's role, and grant or revoke per-repository code access. The server decides
 * what the signed-in user may do and reports it as `capabilities`; the UI hides the
 * controls it does not have, but the server still enforces every rule, so hiding is
 * presentation only and never the security boundary.
 */
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Plus, Shield, Trash2, UserPlus, Users, X } from 'lucide-react';

import { PageHeader } from '@/components/AppLayout';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  Input,
  LoadingState,
  Select,
  Table,
  Td,
  Th,
} from '@/components/ui';
import { api, ApiError } from '@/api/client';
import { useAuth } from '@/context/AuthContext';
import { theme } from '@/lib/theme';
import type { RepoPermission, Team, TeamDetail, TeamRole } from '@/types/api';

const ROLE_HELP: Record<TeamRole, string> = {
  manager: 'Manages this team: adds members and controls which repositories it can reach.',
  member: 'Can use the repositories this team has been granted.',
  viewer: 'Read-only.',
};

const PERMISSION_TONE: Record<RepoPermission, 'neutral' | 'info' | 'primary'> = {
  read: 'neutral',
  write: 'info',
  admin: 'primary',
};

export default function TeamsPage() {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ tone: 'good' | 'critical'; text: string } | null>(
    null,
  );

  const teams = useQuery({
    queryKey: ['teams'],
    queryFn: () => api.teams({ page_size: 100 }),
  });

  const isAdmin = user?.role === 'admin';

  // Default to the first team so the page is never an empty shell.
  const activeId = useMemo(() => {
    if (selectedId !== null) return selectedId;
    const first = teams.data?.items?.[0];
    return first ? first.id : null;
  }, [selectedId, teams.data]);

  const detail = useQuery({
    queryKey: ['team', activeId],
    queryFn: () => api.team(activeId as number),
    enabled: activeId !== null,
  });

  const myAccess = useQuery({
    queryKey: ['my-access'],
    queryFn: () => api.myAccess(),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['teams'] });
    void queryClient.invalidateQueries({ queryKey: ['team', activeId] });
    void queryClient.invalidateQueries({ queryKey: ['my-access'] });
  };

  const fail = (error: unknown) => {
    const text = error instanceof ApiError ? error.detail : 'Something went wrong.';
    setNotice({ tone: 'critical', text });
  };
  const ok = (text: string) => setNotice({ tone: 'good', text });

  return (
    <>
      <PageHeader
        title="Teams & Code Access"
        description="A project manager owns a team, chooses its members, and decides which repositories that team may read or change. Each person's global role caps what their team can grant them."
        actions={<NewTeamButton onCreated={(team) => {
          invalidate();
          setSelectedId(team.id);
          ok(`Created "${team.name}". You are its project manager.`);
        }} onError={fail} />}
      />

      <div style={{ padding: theme.space(6) }}>
        {notice && (
          <div
            role="status"
            style={{
              marginBottom: theme.space(4),
              padding: `${theme.space(2.5)} ${theme.space(3.5)}`,
              borderRadius: theme.radius.md,
              fontSize: 13,
              border: `1px solid ${notice.tone === 'good' ? theme.color.success : theme.color.danger}`,
              background: notice.tone === 'good' ? theme.color.successSoft : theme.color.dangerSoft,
              color: notice.tone === 'good' ? theme.color.success : theme.color.danger,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: theme.space(3),
            }}
          >
            <span>{notice.text}</span>
            <button
              onClick={() => setNotice(null)}
              aria-label="Dismiss message"
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: 'inherit',
                display: 'grid',
                placeItems: 'center',
                padding: 0,
              }}
            >
              <X size={15} />
            </button>
          </div>
        )}

        <YourAccess
          access={myAccess.data}
          loading={myAccess.isLoading}
          isAdmin={isAdmin}
        />

        {teams.isLoading && <LoadingState label="Loading your teams." />}

        {teams.isError && (
          <ErrorState
            error={teams.error instanceof ApiError ? teams.error : new Error('Could not load teams')}
            onRetry={() => void teams.refetch()}
          />
        )}

        {teams.data && teams.data.items.length === 0 && (
          <Card>
            <EmptyState
              title="You are not in any team yet"
              description="A project manager creates a team, then adds you and grants the team access to the repositories you work on. Create the first team to get started."
            />
          </Card>
        )}

        {teams.data && teams.data.items.length > 0 && (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(240px, 300px) minmax(0, 1fr)',
              gap: theme.space(4),
              alignItems: 'start',
            }}
          >
            <TeamList
              teams={teams.data.items}
              activeId={activeId}
              onSelect={setSelectedId}
            />
            <div>
              {detail.isLoading && <LoadingState label="Loading the team." rows={6} />}
              {detail.isError && (
                <ErrorState
                  error={
                    detail.error instanceof ApiError
                      ? detail.error
                      : new Error('Could not load the team')
                  }
                  onRetry={() => void detail.refetch()}
                />
              )}
              {detail.data && (
                <TeamDetailPanel
                  team={detail.data}
                  onChanged={(text) => {
                    invalidate();
                    ok(text);
                  }}
                  onError={fail}
                />
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ your access */

function YourAccess({
  access,
  loading,
  isAdmin,
}: {
  access: Awaited<ReturnType<typeof api.myAccess>> | undefined;
  loading: boolean;
  isAdmin: boolean;
}) {
  if (loading) return null;
  if (!access) return null;

  return (
    <div style={{ marginBottom: theme.space(4) }}>
      <Card>
        <CardHeader
          title={
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
              <Shield size={15} aria-hidden /> Your access
            </span>
          }
        subtitle={
          isAdmin
            ? `You are an administrator, so you can see every repository (${access.repositories.length}).`
            : `You are a ${access.role} and can reach ${access.repositories.length} ${
                access.repositories.length === 1 ? 'repository' : 'repositories'
              }${access.manages_teams > 0 ? `, and you manage ${access.manages_teams} team(s)` : ''}.`
        }
      />
      {access.repositories.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: theme.space(2) }}>
          {access.repositories.map((row) => (
            <span
              key={row.repository_id}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: `${theme.space(1.5)} ${theme.space(2.5)}`,
                border: `1px solid ${theme.color.border}`,
                borderRadius: theme.radius.md,
                fontSize: 12.5,
                background: theme.color.bg,
              }}
            >
              <KeyRound size={12} aria-hidden style={{ color: theme.color.textFaint }} />
              <span style={{ fontWeight: 550 }}>{row.full_name}</span>
              <Badge tone={PERMISSION_TONE[row.permission ?? 'read']}>{row.permission}</Badge>
              {row.source === 'team' && row.via_teams.length > 0 && (
                <span style={{ color: theme.color.textFaint, fontSize: 11.5 }}>
                  via {row.via_teams.join(', ')}
                </span>
              )}
            </span>
          ))}
        </div>
      )}
      </Card>
    </div>
  );
}

/* -------------------------------------------------------------------- team list */

function TeamList({
  teams,
  activeId,
  onSelect,
}: {
  teams: Team[];
  activeId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <div
      className="card"
      style={{ padding: theme.space(2), display: 'grid', gap: 2 }}
    >
        {teams.map((team) => {
          const isActive = team.id === activeId;
          return (
            <button
              key={team.id}
              onClick={() => onSelect(team.id)}
              aria-current={isActive ? 'true' : undefined}
              style={{
                textAlign: 'left',
                padding: theme.space(3),
                borderRadius: theme.radius.md,
                border: `1px solid ${isActive ? theme.color.primary : 'transparent'}`,
                background: isActive ? theme.color.primarySoft : 'transparent',
                cursor: 'pointer',
                fontFamily: 'inherit',
                color: 'inherit',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 620, fontSize: 13.5 }}>{team.name}</span>
                {team.my_role && <Badge tone="info">{team.my_role}</Badge>}
                {!team.is_active && <Badge tone="warning">inactive</Badge>}
              </div>
              <div style={{ marginTop: 3, fontSize: 11.5, color: theme.color.textFaint }}>
                {team.member_count} {team.member_count === 1 ? 'member' : 'members'} ·{' '}
                {team.repository_count}{' '}
                {team.repository_count === 1 ? 'repository' : 'repositories'}
              </div>
            </button>
          );
        })}
    </div>
  );
}

/* ---------------------------------------------------------------- team detail */

function TeamDetailPanel({
  team,
  onChanged,
  onError,
}: {
  team: TeamDetail;
  onChanged: (message: string) => void;
  onError: (error: unknown) => void;
}) {
  const canManageMembers = team.capabilities.includes('manage_members');
  const canManageAccess = team.capabilities.includes('manage_access');

  return (
    <div style={{ display: 'grid', gap: theme.space(4) }}>
      <Card>
        <CardHeader
          title={
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
              <Users size={15} aria-hidden /> {team.name}
            </span>
          }
          subtitle={team.description ?? 'No description.'}
        />
        <div style={{ display: 'flex', gap: theme.space(4), flexWrap: 'wrap', fontSize: 12.5 }}>
          <span style={{ color: theme.color.textMuted }}>
            Project manager:{' '}
            <strong style={{ color: theme.color.text }}>
              {team.manager_username ?? 'unassigned'}
            </strong>
          </span>
          <span style={{ color: theme.color.textMuted }}>
            Your role: <strong style={{ color: theme.color.text }}>{team.my_role ?? 'none'}</strong>
          </span>
          {!canManageMembers && (
            <span style={{ color: theme.color.textFaint }}>
              You have read-only visibility of this team.
            </span>
          )}
        </div>
      </Card>

      <MembersCard team={team} canManage={canManageMembers} onChanged={onChanged} onError={onError} />
      <AccessCard team={team} canManage={canManageAccess} onChanged={onChanged} onError={onError} />
    </div>
  );
}

/* --------------------------------------------------------------------- members */

function MembersCard({
  team,
  canManage,
  onChanged,
  onError,
}: {
  team: TeamDetail;
  canManage: boolean;
  onChanged: (message: string) => void;
  onError: (error: unknown) => void;
}) {
  const [username, setUsername] = useState('');
  const [role, setRole] = useState<TeamRole>('member');

  const add = useMutation({
    mutationFn: () => api.addTeamMember(team.id, { username: username.trim(), role }),
    onSuccess: (member) => {
      setUsername('');
      onChanged(`Added ${member.username} as ${member.role}.`);
    },
    onError,
  });

  const changeRole = useMutation({
    mutationFn: ({ userId, next }: { userId: number; next: TeamRole }) =>
      api.updateTeamMember(team.id, userId, { role: next }),
    onSuccess: (member) => onChanged(`${member.username} is now a ${member.role}.`),
    onError,
  });

  const remove = useMutation({
    mutationFn: (userId: number) => api.removeTeamMember(team.id, userId),
    onSuccess: (_result, userId) => {
      const member = team.members.find((m) => m.user_id === userId);
      onChanged(`Removed ${member?.username ?? 'the member'} from ${team.name}.`);
    },
    onError,
  });

  const busy = add.isPending || changeRole.isPending || remove.isPending;

  return (
    <Card>
      <CardHeader
        title="Members"
        subtitle="A manager runs the team, a member can use the team's repositories, and a viewer can only read."
      />

      {canManage && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (username.trim()) add.mutate();
          }}
          style={{
            display: 'flex',
            gap: theme.space(2),
            alignItems: 'flex-end',
            flexWrap: 'wrap',
            marginBottom: theme.space(4),
            paddingBottom: theme.space(4),
            borderBottom: `1px solid ${theme.color.border}`,
          }}
        >
          <div style={{ flex: '1 1 220px' }}>
            <Input
              id="member-username"
              label="Username or email"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="raj.dev"
              required
            />
          </div>
          <div style={{ flex: '0 0 150px' }}>
            <Select
              id="member-role"
              label="Team role"
              value={role}
              onChange={(e) => setRole(e.target.value as TeamRole)}
            >
              <option value="member">member</option>
              <option value="viewer">viewer</option>
              <option value="manager">manager</option>
            </Select>
          </div>
          <Button type="submit" disabled={busy || !username.trim()} loading={add.isPending}>
            <UserPlus size={14} aria-hidden /> Add member
          </Button>
        </form>
      )}

      <Table>
        <thead>
          <tr>
            <Th>Member</Th>
            <Th>Role</Th>
            <Th>Scope</Th>
            {canManage && <Th align="right">Manage</Th>}
          </tr>
        </thead>
        <tbody>
          {team.members.map((member) => (
            <tr key={member.id}>
              <Td>
                <div style={{ fontWeight: 570 }}>{member.username}</div>
                <div style={{ fontSize: 11.5, color: theme.color.textFaint }}>{member.email}</div>
              </Td>
              <Td>
                {canManage ? (
                  <select
                    value={member.role}
                    disabled={busy}
                    title={ROLE_HELP[member.role]}
                    aria-label={`Role for ${member.username}`}
                    onChange={(e) =>
                      changeRole.mutate({ userId: member.user_id, next: e.target.value as TeamRole })
                    }
                    style={{
                      padding: '5px 7px',
                      fontSize: 12.5,
                      fontFamily: 'inherit',
                      borderRadius: theme.radius.sm,
                      border: `1px solid ${theme.color.border}`,
                      background: theme.color.bg,
                      color: theme.color.text,
                    }}
                  >
                    <option value="manager">manager</option>
                    <option value="member">member</option>
                    <option value="viewer">viewer</option>
                  </select>
                ) : (
                  <Badge tone={member.role === 'manager' ? 'primary' : 'neutral'}>
                    {member.role}
                  </Badge>
                )}
              </Td>
              <Td>
                {member.scoped_repository_id ? (
                  <Badge tone="warning">repo #{member.scoped_repository_id}</Badge>
                ) : (
                  <span style={{ color: theme.color.textFaint, fontSize: 12 }}>all team repos</span>
                )}
              </Td>
              {canManage && (
                <Td align="right">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => remove.mutate(member.user_id)}
                    title={`Remove ${member.username}`}
                  >
                    <Trash2 size={13} aria-hidden />
                    <span className="sr-only">Remove {member.username}</span>
                  </Button>
                </Td>
              )}
            </tr>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}

/* ---------------------------------------------------------------- code access */

function AccessCard({
  team,
  canManage,
  onChanged,
  onError,
}: {
  team: TeamDetail;
  canManage: boolean;
  onChanged: (message: string) => void;
  onError: (error: unknown) => void;
}) {
  const repositories = useQuery({
    queryKey: ['repositories-for-access'],
    queryFn: () => api.repositories({ page_size: 100, sort: 'name' }),
  });
  const [repositoryId, setRepositoryId] = useState('');
  const [permission, setPermission] = useState<RepoPermission>('read');

  const grant = useMutation({
    mutationFn: () =>
      api.grantTeamRepository(team.id, {
        repository_id: Number(repositoryId),
        permission,
      }),
    onSuccess: (row) => {
      setRepositoryId('');
      onChanged(`Granted "${row.repository_name}" to ${team.name} with ${row.permission}.`);
    },
    onError,
  });

  const revoke = useMutation({
    mutationFn: (grantId: number) => api.revokeTeamRepository(team.id, grantId),
    onSuccess: (_r, grantId) => {
      const row = team.repositories.find((g) => g.id === grantId);
      onChanged(`Revoked ${team.name}'s access to "${row?.repository_name ?? 'the repository'}".`);
    },
    onError,
  });

  // Offer only repositories the team does not already hold a grant on.
  const granted = new Set(team.repositories.map((g) => g.repository_id));
  const options = (repositories.data?.items ?? []).filter((r) => !granted.has(r.id));

  return (
    <Card>
      <CardHeader
        title="Repository access"
        subtitle="A team reaches a repository only when it has been granted here. Managing the team never widens that set."
      />

      {canManage && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (repositoryId) grant.mutate();
          }}
          style={{
            display: 'flex',
            gap: theme.space(2),
            alignItems: 'flex-end',
            flexWrap: 'wrap',
            marginBottom: theme.space(4),
            paddingBottom: theme.space(4),
            borderBottom: `1px solid ${theme.color.border}`,
          }}
        >
          <div style={{ flex: '1 1 260px' }}>
            <Select
              id="grant-repo"
              label="Repository"
              value={repositoryId}
              onChange={(e) => setRepositoryId(e.target.value)}
              required
            >
              <option value="">Choose a repository…</option>
              {options.map((repo) => (
                <option key={repo.id} value={repo.id}>
                  {repo.full_name}
                </option>
              ))}
            </Select>
          </div>
          <div style={{ flex: '0 0 140px' }}>
            <Select
              id="grant-permission"
              label="Permission"
              value={permission}
              onChange={(e) => setPermission(e.target.value as RepoPermission)}
            >
              <option value="read">read</option>
              <option value="write">write</option>
              <option value="admin">admin</option>
            </Select>
          </div>
          <Button type="submit" disabled={grant.isPending || !repositoryId} loading={grant.isPending}>
            <Plus size={14} aria-hidden /> Grant access
          </Button>
        </form>
      )}

      {team.repositories.length === 0 ? (
        <p style={{ margin: 0, fontSize: 13, color: theme.color.textMuted }}>
          This team cannot reach any repository, so its members see nothing yet.
        </p>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Repository</Th>
              <Th>Permission</Th>
              <Th>Note</Th>
              {canManage && <Th align="right">Revoke</Th>}
            </tr>
          </thead>
          <tbody>
            {team.repositories.map((row) => (
              <tr key={row.id}>
                <Td>
                  <span style={{ fontWeight: 550 }}>{row.repository_name}</span>
                </Td>
                <Td>
                  <Badge tone={PERMISSION_TONE[row.permission]}>{row.permission}</Badge>
                  {row.is_expired && (
                    <span style={{ marginLeft: 6 }}>
                      <Badge tone="warning">expired</Badge>
                    </span>
                  )}
                </Td>
                <Td>
                  <span style={{ color: theme.color.textFaint, fontSize: 12 }}>
                    {row.note ?? '—'}
                  </span>
                </Td>
                {canManage && (
                  <Td align="right">
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={revoke.isPending}
                      onClick={() => revoke.mutate(row.id)}
                      title={`Revoke access to ${row.repository_name}`}
                    >
                      <Trash2 size={13} aria-hidden />
                      <span className="sr-only">Revoke {row.repository_name}</span>
                    </Button>
                  </Td>
                )}
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

/* ----------------------------------------------------------------- new team */

function NewTeamButton({
  onCreated,
  onError,
}: {
  onCreated: (team: Team) => void;
  onError: (error: unknown) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  const create = useMutation({
    mutationFn: () =>
      api.createTeam({
        name: name.trim(),
        description: description.trim() || undefined,
      }),
    onSuccess: (team) => {
      setOpen(false);
      setName('');
      setDescription('');
      onCreated(team);
    },
    onError,
  });

  if (!open) {
    return (
      <Button onClick={() => setOpen(true)}>
        <Plus size={14} aria-hidden /> New team
      </Button>
    );
  }

  return (
    <div
      className="card"
      style={{
        width: 360,
        padding: theme.space(4),
        display: 'grid',
        gap: theme.space(3),
      }}
    >
      <div style={{ fontSize: 13, fontWeight: 620 }}>Create a team</div>
      <Input
        id="team-name"
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Platform Engineering"
        required
        autoFocus
      />
      <Input
        id="team-description"
        label="Description (optional)"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Owns the shared CLI tooling."
      />
      <div style={{ display: 'flex', gap: theme.space(2), justifyContent: 'flex-end' }}>
        <Button variant="secondary" onClick={() => setOpen(false)}>
          Cancel
        </Button>
        <Button
          onClick={() => create.mutate()}
          disabled={!name.trim() || create.isPending}
          loading={create.isPending}
        >
          Create
        </Button>
      </div>
    </div>
  );
}
