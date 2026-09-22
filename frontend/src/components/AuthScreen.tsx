import { useState } from "react";

import {
    login,
    register,
    type User,
} from "../api/auth";


interface AuthScreenProps {
    onAuthenticated: (user: User) => void;
}


export function AuthScreen({
    onAuthenticated,
}: AuthScreenProps) {
    const [mode, setMode] =
        useState<"login" | "register">("login");

    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [name, setName] = useState("");

    const [submitting, setSubmitting] =
        useState(false);

    const [error, setError] =
        useState<string | null>(null);


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
                </div>


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

            </div>

        </div>
    );
}