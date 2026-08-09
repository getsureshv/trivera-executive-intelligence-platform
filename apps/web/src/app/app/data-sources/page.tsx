import { redirect } from 'next/navigation';

import { ApiError, fetchDataSources } from '@/lib/api';

import { DataSourceManager } from './DataSourceManager';

export const dynamic = 'force-dynamic';

export default async function DataSourcesPage() {
  try {
    return <DataSourceManager initialSources={await fetchDataSources()} />;
  } catch (error) {
    if (error instanceof ApiError && error.isUnauthenticated) redirect('/sign-in');
    if (error instanceof ApiError && error.isForbidden) {
      return <p className="notice notice--error">You are not permitted to view data sources.</p>;
    }
    throw error;
  }
}
