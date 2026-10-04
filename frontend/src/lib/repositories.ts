import type { Repository } from '@/types/api';

/**
 * The repository a view should show when the URL does not name one.
 *
 * `api.repositories()` returns the list in whatever order the API sorts by, and each
 * page asks for a different sort, so taking `items[0]` - or letting any tie fall to list
 * order - makes the same product look different from one page to the next. `items[0]`
 * also lands on whichever repository sorts first, often one that was just added and
 * holds a couple of commits, so every tile reads zero and the page looks broken.
 *
 * Preference order:
 *   1. most ingested history,
 *   2. then the most recently active.
 *
 * Both are needed. `seed_local.py` caps ingestion at 900 commits per repository, so all
 * five OSS repositories tie exactly on (1) - and `health_score` ties too. Ranking on
 * recency alone would pick a repository with three commits from last week, so recency
 * only breaks the tie.
 *
 * The final tie-break is the lowest id, so a repository whose history has not been
 * ingested yet never outranks one that has, and the choice is stable across pages.
 * The user can still switch to any repository.
 */
export function defaultRepositoryId(items: Repository[] | undefined): number | undefined {
  if (!items?.length) return undefined;
  return items.reduce((best, repo) => {
    if (repo.ingested_commits !== best.ingested_commits) {
      return repo.ingested_commits > best.ingested_commits ? repo : best;
    }
    const a = repo.latest_commit_at ? Date.parse(repo.latest_commit_at) : 0;
    const b = best.latest_commit_at ? Date.parse(best.latest_commit_at) : 0;
    if (a !== b) return a > b ? repo : best;
    return repo.id < best.id ? repo : best;
  }).id;
}