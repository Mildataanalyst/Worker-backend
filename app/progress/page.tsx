import ProgressClient from './ProgressClient';
import Header from '@/components/Header';
import { getDashboardData } from '@/lib/dashboardStore';

export const dynamic = 'force-dynamic';

export default async function ProgressPage(){
  const data = await getDashboardData();
  return <>
    <Header active="ranking" />
    <ProgressClient initialData={data} />
  </>;
}
