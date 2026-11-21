import { Suspense } from 'react';
import { Chat } from '@/components/campus/chat';
export default function Account(){return <Suspense fallback={<p className="page-loading">Loading your campus assistant…</p>}><Chat/></Suspense>;}
