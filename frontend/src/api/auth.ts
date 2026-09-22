export interface User {
    user_id: string;
    email: string;
    name: string | null;
}

export interface RegisterInput {
    email: string;
    password: string;
    name?: string;
}

export interface LoginInput {
    email: string;
    password: string;
}


async function readError(
    response: Response
): Promise<string> {
    try {
        const body = await response.json();

        if (typeof body.detail === "string") {
            return body.detail;
        }
    } catch {
        // Ignore malformed/non-JSON error body.
    }

    return "Request failed";
}


export async function getCurrentUser(): Promise<User | null> {
    const response = await fetch("/v2/auth/me");

    if (response.status === 401) {
        return null;
    }

    if (!response.ok) {
        throw new Error(
            await readError(response)
        );
    }

    return response.json();
}


export async function login(
    input: LoginInput
): Promise<User> {
    const response = await fetch(
        "/v2/auth/login",
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(input),
        }
    );

    if (!response.ok) {
        throw new Error(
            await readError(response)
        );
    }

    return response.json();
}


export async function register(
    input: RegisterInput
): Promise<User> {
    const response = await fetch(
        "/v2/auth/register",
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(input),
        }
    );

    if (!response.ok) {
        throw new Error(
            await readError(response)
        );
    }

    return response.json();
}


export async function logout(): Promise<void> {
    const response = await fetch(
        "/v2/auth/logout",
        {
            method: "POST",
        }
    );

    if (!response.ok) {
        throw new Error(
            await readError(response)
        );
    }
}