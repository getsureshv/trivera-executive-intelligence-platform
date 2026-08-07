import { redirect } from 'next/navigation';

/** The root is not a landing page; the authenticated experience lives at /app. */
export default function Home() {
  redirect('/app');
}
