import type { Repository } from '@/types/api';

/**
 * The repository a view should show when the URL does not name one.
 *
 * `api.repositories()` returns the list in whatever order the API sorts by, and each
 * page asks for a different sort, so `items[0]` and any tie would resolve differently
 * from one view to the next. Taking `items[0]` also lands on whichever repository
 * sorts first - often one that was just added and holds a couple of commits - so every
 * tile reads zero and the page looks broken.
 *
 * The repository with the most ingested history is the meaningful default. Because
 * `seed_local.py` caps ingestion at 900 commits per repository, the five OSS
 * repositories tie exactly, so the tie is broken on the lowest id to keep every
 * repository-scoped page on the same repository regardless of list order. The user can
 * still switch to any repository.
 */
export function defaultRepositoryId(items: Repository[] | undefined): number | undefined {
  if (!items?.length) return undefined;
  return items.reduce((best, repo) => {
    if (repo.ingested_commits !== best.ingested_commits) {
      return repo.ingested_commits > best.ingested_commits ? repo : best;
    }
    return repo.id < best.id ? repo : best;
  }).id;
}