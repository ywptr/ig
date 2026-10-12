import { useEffect, useRef, useState } from "react";

import {
    getTenantAuthConfig,
    googleLogin,
    login,
    register,
    type TenantAuthConfig,
    type User,
} from "../api/auth";


interface AuthScreenProps {
    onAuthenticated: (user: User) => void;
}


export function AuthScreen({
    onAuthenticated,
}: AuthScreenProps) {
    const [mode, setMode] = useState<"login" | "register">("login");

    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [name, setName] = useState("");

    const [submitting, setSubmitting] = useState(false);

    const [error, setError] = useState<string | null>(null);

    const googleButtonRef = useRef<HTMLDivElement | null>(null);

    const [authConfig, setAuthConfig] = useState<TenantAuthConfig | null>(null);

    const [loadingConfig, setLoadingConfig] = useState(true);

    useEffect(() => {
        let cancelled = false;

        async function loadAuthConfig() {
            try {
                setLoadingConfig(true);
                setError(null);

                const config =
                    await getTenantAuthConfig();

                if (!cancelled) {
                    setAuthConfig(config);
                }
            } catch (error) {
                if (!cancelled) {
                    setError(
                        error instanceof Error
                            ? error.message
                            : "Unable to load authentication configuration"
                    );
                }
            } finally {
                if (!cancelled) {
                    setLoadingConfig(false);
                }
            }
        }

        void loadAuthConfig();

        return () => {
            cancelled = true;
        };
    }, []);

    useEffect(() => {
        if (
            authConfig
            && !authConfig.allow_self_registration
            && mode === "register"
        ) {
            setMode("login");
        }
    }, [
        authConfig,
        mode,
    ]);

    useEffect(() => {
        const clientId = authConfig?.google.client_id;

        if (
            !authConfig?.google.enabled
            || typeof clientId !== "string"
            || !googleButtonRef.current
        ) {
            return;
        }
        const googleClientId: string = clientId;

        let intervalId: number | undefined;

        function tryRenderGoogleButton() {
            const google = (
                window as typeof window & {
                    google?: {
                        accounts: {
                            id: {
                                initialize: (
                                    options: {
                                        client_id: string;
                                        callback: (
                                            response: {
                                                credential: string;
                                            }
                                        ) => void;
                                    }
                                ) => void;

                                renderButton: (
                                    element: HTMLElement,
                                    options: {
                                        theme?: string;
                                        size?: string;
                                        width?: number;
                                    }
                                ) => void;
                            };
                        };
                    };
                }
            ).google;

            if (
                !google
                || !googleButtonRef.current
            ) {
                return false;
            }

            google.accounts.id.initialize({
                client_id: googleClientId,
                callback: async (response) => {
                    try {
                        setSubmitting(true);
                        setError(null);

                        const user = await googleLogin({
                            credential:
                                response.credential,
                        });

                        onAuthenticated(user);
                    } catch (error) {
                        setError(
                            error instanceof Error
                                ? error.message
                                : "Google sign-in failed"
                        );
                    } finally {
                        setSubmitting(false);
                    }
                },
            });

            google.accounts.id.renderButton(
                googleButtonRef.current,
                {
                    theme: "outline",
                    size: "large",
                    width: 320,
                }
            );

            return true;
        }

        if (!tryRenderGoogleButton()) {
            intervalId = window.setInterval(
                () => {
                    if (tryRenderGoogleButton()) {
                        if (intervalId !== undefined) {
                            window.clearInterval(
                                intervalId
                            );
                        }
                    }
                },
                100
            );
        }

        return () => {
            if (intervalId !== undefined) {
                window.clearInterval(intervalId);
            }
        };
    }, [
        authConfig,
        onAuthenticated,
    ]);

    async function handleSubmit(
        event: React.FormEvent
    ) {
        event.preventDefault();

        if (submitting) {
            return;
        }

        try {
            setSubmitting(true);
            setError(null);

            if (mode === "login") {
                const user = await login({
                    email,
                    password,
                });

                onAuthenticated(user);
                return;
            }

            await register({
                email,
                password,
                name: name.trim() || undefined,
            });

            /*
             * Registration does not create a session yet,
             * so log in immediately afterward.
             */
            const user = await login({
                email,
                password,
            });

            onAuthenticated(user);

        } catch (error) {
            setError(
                error instanceof Error
                    ? error.message
                    : "Authentication failed"
            );

        } finally {
            setSubmitting(false);
        }
    }


    function switchMode(
        nextMode: "login" | "register"
    ) {
        setMode(nextMode);
        setError(null);
    }

    if (loadingConfig) {
        return (
            <div className="auth-page">
                <div className="auth-card">
                    Loading authentication options…
                </div>
            </div>
        );
    }

    if (
        authConfig
        && !authConfig.password.enabled
        && !authConfig.google.enabled
    ) {
        return (
            <div className="auth-page">
                <div className="auth-card">
                    <div className="error">
                        No authentication method is available.
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="auth-page">

            <div className="auth-brand">
                <span className="brand-name">
                    IG
                </span>

                <span className="brand-subtitle">
                    AI Image Generator
                </span>
            </div>



            <div className="auth-card">
            {authConfig?.password.enabled && (
                <div className="auth-tabs">
                    <button
                        type="button"
                        className={
                            mode === "login"
                                ? "active"
                                : ""
                        }
                        onClick={() =>
                            switchMode("login")
                        }
                    >
                        Sign in
                    </button>

                {authConfig?.allow_self_registration && (
                    <button
                        type="button"
                        className={
                            mode === "register"
                                ? "active"
                                : ""
                        }
                        onClick={() =>
                            switchMode("register")
                        }
                    >
                        Create account
                    </button>
                    )}
                </div>
            )}
            
            {authConfig?.google.enabled && (

                <div className="google-auth">
                    <div ref={googleButtonRef} />

                    {authConfig.password.enabled && (
                    <div className="auth-divider">
                        <span>or</span>
                    </div>
                    )
                    }
                </div>
            )}

                <div className="auth-heading">
                    <h1>
                        {mode === "login"
                            ? "Welcome back"
                            : "Create your account"}
                    </h1>

                    <p>
                        {mode === "login"
                            ? "Sign in to continue to IG."
                            : "Create an account to start generating images."}
                    </p>
                </div>

            {authConfig?.password.enabled && (
                <form
                    className="auth-form"
                    onSubmit={handleSubmit}
                >

                    {mode === "register" && (
                        <label>
                            <span>Name</span>

                            <input
                                type="text"
                                value={name}
                                onChange={(event) =>
                                    setName(
                                        event.target.value
                                    )
                                }
                                autoComplete="name"
                            />
                        </label>
                    )}


                    <label>
                        <span>Email</span>

                        <input
                            type="email"
                            value={email}
                            onChange={(event) =>
                                setEmail(
                                    event.target.value
                                )
                            }
                            autoComplete="email"
                            required
                        />
                    </label>


                    <label>
                        <span>Password</span>

                        <input
                            type="password"
                            value={password}
                            onChange={(event) =>
                                setPassword(
                                    event.target.value
                                )
                            }
                            autoComplete={
                                mode === "login"
                                    ? "current-password"
                                    : "new-password"
                            }
                            minLength={8}
                            required
                        />
                    </label>


                    {error && (
                        <div className="error">
                            {error}
                        </div>
                    )}


                    <button
                        className="auth-submit"
                        type="submit"
                        disabled={submitting}
                    >
                        {submitting
                            ? "Please wait…"
                            : mode === "login"
                                ? "Sign in"
                                : "Create account"}
                    </button>

                </form>
            )}
            </div>

        </div>
    );
}