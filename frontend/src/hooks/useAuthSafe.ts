/** Small wrapper so pages can import auth without a hard dependency on the context module. */
import { useAuth } from '@/context/AuthContext';

export { ApiError } from '@/api/client';
export const useAuthSafe = useAuth;
