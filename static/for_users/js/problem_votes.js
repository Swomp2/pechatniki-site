(function () {
  const voteFormSelector = "[data-problem-vote-form]";
  const voteButtonSelector = "[data-vote-button]";
  const voteCountSelector = "[data-vote-count]";

  const voteStates = new WeakMap();

  document.addEventListener("submit", handleVoteSubmit);

  function handleVoteSubmit(event) {
    const form = event.target.closest?.(voteFormSelector);

    if (!form || !window.fetch || !window.FormData) {
      return;
    }

    event.preventDefault();

    const controls = getVoteControls(form);

    if (!controls) {
      form.submit();
      return;
    }

    const state = getVoteState(form, controls);

    if (state.inFlight) {
      return;
    }

    const desiredVoted = !state.confirmedVoted;

    state.inFlight = true;
    setPendingState(form, controls.button, true);

    sendDesiredVote(form, desiredVoted)
      .then(function (response) {
        if (!response.response.ok || !hasVoteState(response.data)) {
          throw new Error("vote_rejected");
        }

        updateProblemVoteState(response.data);
        document.dispatchEvent(new CustomEvent("site:content-mutated"));
      })
      .catch(function () {
        setVoteState(controls.button, controls.countElement, {
          voted: state.confirmedVoted,
          votesCount: state.confirmedCount,
        });
        showVoteError();
      })
      .finally(function () {
        state.inFlight = false;
        setPendingState(form, controls.button, false);
      });
  }

  function getVoteControls(form) {
    const button = form.querySelector(voteButtonSelector);
    const countElement = button?.querySelector(voteCountSelector);

    if (!button || !countElement) {
      return null;
    }

    return {
      button,
      countElement,
    };
  }

  function getVoteState(form, controls) {
    let state = voteStates.get(form);

    if (state) {
      return state;
    }

    const initialVoted = controls.button.getAttribute("aria-pressed") === "true";
    const initialCount = parseVoteCount(controls.countElement.textContent);

    state = {
      confirmedVoted: initialVoted,
      confirmedCount: initialCount,
      inFlight: false,
    };

    voteStates.set(form, state);

    return state;
  }

  async function sendDesiredVote(form, desiredVoted) {
    const formData = new FormData(form);

    formData.set("desired_voted", desiredVoted ? "1" : "0");

    const response = await fetch(form.action, {
      method: "POST",
      body: formData,
      headers: {
        Accept: "application/json",
        "X-Requested-With": "fetch",
      },
      credentials: "same-origin",
    });

    return {
      response,
      data: await readJson(response),
    };
  }

  function setVoteState(button, countElement, state) {
    const votesCount = Number.parseInt(state.votesCount, 10);

    button.setAttribute("aria-pressed", state.voted ? "true" : "false");
    countElement.textContent = Number.isFinite(votesCount)
      ? String(Math.max(0, votesCount))
      : "0";
  }

  function setPendingState(form, button, isPending) {
    form.dataset.votePending = isPending ? "true" : "false";
    button.classList.toggle("is-pending", isPending);
    button.setAttribute("aria-busy", isPending ? "true" : "false");
    button.disabled = isPending;
  }

  function parseVoteCount(value) {
    const count = Number.parseInt(value, 10);

    return Number.isFinite(count) ? count : 0;
  }

  async function readJson(response) {
    const contentType = response.headers.get("content-type") || "";

    if (!contentType.includes("application/json")) {
      return null;
    }

    return response.json();
  }

  function hasVoteState(data) {
    return (
      data &&
      typeof data.voted === "boolean" &&
      Number.isFinite(Number.parseInt(data.votes_count, 10))
    );
  }

  function updateProblemVoteState(data) {
    const problemId = String(data.problem_id);
    const selector = `${voteFormSelector}[data-problem-id="${problemId}"]`;

    document.querySelectorAll(selector).forEach(function (form) {
      const controls = getVoteControls(form);

      if (!controls) {
        return;
      }

      const state = getVoteState(form, controls);

      state.confirmedVoted = data.voted;
      state.confirmedCount = parseVoteCount(data.votes_count);

      setVoteState(controls.button, controls.countElement, {
        voted: state.confirmedVoted,
        votesCount: state.confirmedCount,
      });
    });
  }

  function showVoteError() {
    window.alert("Не удалось изменить важность проблемы. Попробуйте ещё раз.");
  }
})();
