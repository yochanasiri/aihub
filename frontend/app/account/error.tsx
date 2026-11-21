'use client';
import { Button } from '@/components/ui/button';
export default function ErrorPage({reset}:{reset:()=>void}){return <div className="information-page"><div className="empty-panel"><h2>This page couldn’t load</h2><p>Please try again. If the problem continues, contact your AIHUB administrator.</p><Button onClick={reset}>Try again</Button></div></div>;}
