package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

type app struct {
	globals      Globals
	cfg          Config
	client       *Client
	defaultModel string
}

type commandExit struct {
	code int
}

func (e commandExit) Error() string { return fmt.Sprintf("exit code %d", e.code) }

func newRootCmd() *cobra.Command {
	a := &app{}
	root := &cobra.Command{
		Use:           "comfy-rest-ext",
		Short:         "OpenAI-compatible ComfyUI workflow client",
		Long:          "Remote CLI for the comfy-rest-ext ComfyUI extension: discovery, images, videos, provisioning, snapshots, and maintenance.",
		Version:       version,
		SilenceUsage:  true,
		SilenceErrors: true,
		Args:          cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return cmd.Help()
		},
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			return a.prepare()
		},
	}
	root.SetVersionTemplate("{{.Version}}\n")
	root.CompletionOptions.DisableDefaultCmd = true

	root.PersistentFlags().StringVar(&a.globals.BaseURL, "base-url", "",
		optional("server base URL", "COMFY_REST_EXT_BASE_URL, then profile base_url, then http://127.0.0.1:8188"))
	root.PersistentFlags().StringVar(&a.globals.Profile, "profile", "",
		optional("config profile name", "COMFY_REST_EXT_PROFILE, then the selected profile"))
	root.PersistentFlags().StringVar(&a.globals.APIKey, "api-key", "",
		optional("API key; use - to read it from stdin", "COMFY_REST_EXT_API_KEY, then profile api_key"))
	root.PersistentFlags().Float64Var(&a.globals.Timeout, "timeout", 0,
		optional("HTTP timeout in seconds", "COMFY_REST_EXT_TIMEOUT, then profile timeout, then 60"))
	root.PersistentFlags().BoolVar(&a.globals.JSON, "json", false,
		optional("machine-readable JSON output", "human-readable text"))

	root.AddCommand(
		a.healthCmd(),
		a.capabilitiesCmd(),
		a.doctorCmd(),
		a.provisionCmd(),
		a.restartCmd(),
		a.waitReconnectCmd(),
		a.downloadsCmd(),
		a.modelsCmd(),
		a.videosCmd(),
		a.imagesCmd(),
		a.depsCmd(),
		a.snapshotCmd(),
		a.nodesCmd(),
		a.prCacheCmd(),
		a.configCmd(),
		a.versionCmd(),
	)
	return root
}

func (a *app) prepare() error {
	cfg, err := loadConfig()
	if err != nil {
		return err
	}
	a.cfg = cfg
	if a.globals.APIKey == "-" {
		data, err := io.ReadAll(os.Stdin)
		if err != nil {
			return err
		}
		a.globals.APIKey = strings.TrimRight(string(data), "\r\n")
	}
	resolved := resolveProfile(cfg, a.globals)
	a.client = NewClient(resolved)
	a.defaultModel = resolved.DefaultModel
	return nil
}

func (a *app) result(rc int) error {
	if rc == 0 {
		return nil
	}
	return commandExit{code: rc}
}

func (a *app) helpExit(cmd *cobra.Command) error {
	_ = cmd.Help()
	return commandExit{code: 2}
}

func optional(desc, omitted string) string {
	if omitted == "" {
		return desc
	}
	return desc + " (omitted: " + omitted + ")"
}

func (a *app) healthCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "health",
		Short: "Check extension health",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.health())
		},
	}
}

func (a *app) health() int {
	m, err := a.client.getJSON("/v2/extension/health", nil)
	if err != nil {
		return fail(err, a.globals.JSON, 1)
	}
	if a.globals.JSON {
		return emitJSON(m)
	}
	fmt.Println("status:   ", m["status"])
	fmt.Println("extension:", m["extension"])
	if v, ok := m["boot_id"]; ok {
		fmt.Println("boot_id:  ", v)
	}
	return 0
}

func (a *app) capabilitiesCmd() *cobra.Command {
	var model, capability string
	var includeUnavailable bool
	cmd := &cobra.Command{
		Use:   "capabilities",
		Short: "List capabilities and provisioning status",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			q := url.Values{}
			if model != "" {
				q.Set("model", model)
			}
			if capability != "" {
				q.Set("capability", capability)
			}
			if includeUnavailable {
				q.Set("include_unavailable", "1")
			}
			m, err := a.client.getJSON("/v2/extension/capabilities", q)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			if summary, ok := m["summary"].(map[string]any); ok {
				for k, v := range summary {
					fmt.Printf("%s=%v\n", k, v)
				}
			}
			if items, ok := m["capabilities"].([]any); ok {
				for _, item := range items {
					c, _ := item.(map[string]any)
					fmt.Printf("%-10v %v\n", c["status"], c["id"])
				}
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&model, "model", "",
		optional("filter by model id", "all models are included"))
	cmd.Flags().StringVar(&capability, "capability", "",
		optional("filter by capability id", "all capabilities are included"))
	cmd.Flags().BoolVar(&includeUnavailable, "include-unavailable", false,
		optional("include the provisioning inventory when no model is servable", "only servable models are reported"))
	return cmd
}

func (a *app) doctorCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "doctor",
		Short: "Check health, capabilities, and provisioners",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.doctor())
		},
	}
}

func (a *app) doctor() int {
	health, err := a.client.getJSON("/v2/extension/health", nil)
	if err != nil {
		return fail(err, a.globals.JSON, 1)
	}
	caps, err := a.client.getJSON("/v2/extension/capabilities", nil)
	if err != nil {
		return fail(err, a.globals.JSON, 1)
	}
	prov, provErr := a.client.getJSON("/v2/extension/provisioners", nil)
	out := map[string]any{"health": health, "capabilities": caps}
	if provErr == nil {
		out["provisioners"] = prov
	}
	if a.globals.JSON {
		return emitJSON(out)
	}
	fmt.Println("health:", health["status"])
	if summary, ok := caps["summary"].(map[string]any); ok {
		for k, v := range summary {
			fmt.Printf("capabilities %s=%v\n", k, v)
		}
	}
	if provErr != nil {
		fmt.Println("provisioners: unavailable")
	}
	return 0
}

func (a *app) provisionCmd() *cobra.Command {
	var yes bool
	var retryTask string
	cmd := &cobra.Command{
		Use:   "provision CAPABILITY...",
		Short: "Install one or more capabilities",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			payload := map[string]any{"capabilities": args, "authorized": yes}
			if retryTask != "" {
				payload["retry_task_id"] = retryTask
			}
			m, err := a.client.postJSON("/v2/extension/provision", payload)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("status:", m["status"])
			if actions, ok := m["actions"].([]any); ok {
				for _, action := range actions {
					fmt.Println("installed:", action)
				}
			}
			if v, ok := m["restart_required"]; ok && v == true {
				fmt.Println("restart required")
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false,
		optional("explicitly authorize installation", "not authorized; the server returns a 409 authorization plan"))
	cmd.Flags().StringVar(&retryTask, "retry-task", "",
		optional("resume this blocked video task after provisioning", "no task is resumed"))
	return cmd
}

func (a *app) restartCmd() *cobra.Command {
	var yes bool
	var retryTask, reason string
	var timeout, interval float64
	cmd := &cobra.Command{
		Use:   "restart",
		Short: "Restart ComfyUI and wait for reconnection",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			payload := map[string]any{"authorized": yes, "confirm": "restart"}
			if retryTask != "" {
				payload["retry_task_id"] = retryTask
			}
			if reason != "" {
				payload["reason"] = reason
			}
			m, err := a.client.postJSON("/v2/extension/restart", payload)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			oldBoot := fmt.Sprint(m["boot_id"])
			if oldBoot == "" || oldBoot == "<nil>" {
				return a.result(fail(fmt.Errorf("restart response did not include a boot_id"), a.globals.JSON, 1))
			}
			if _, err := waitHealth(a.client, oldBoot, timeout, interval); err != nil {
				return a.result(fail(err, a.globals.JSON, 5))
			}
			if retryTask != "" {
				task, err := retryVideo(a.client, retryTask)
				if err != nil {
					return a.result(fail(err, a.globals.JSON, 1))
				}
				if a.globals.JSON {
					return a.result(emitJSON(task))
				}
				printStatus(task)
				return nil
			}
			if a.globals.JSON {
				return a.result(emitJSON(map[string]any{"status": "restarted", "backend": m["backend"]}))
			}
			fmt.Println("restarted via", m["backend"])
			return nil
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false,
		optional("explicitly authorize restart", "not authorized; the server returns a 409 authorization plan"))
	cmd.Flags().StringVar(&retryTask, "retry-task", "",
		optional("retry this blocked video task after reconnect", "no task is retried"))
	cmd.Flags().StringVar(&reason, "reason", "",
		optional("reason recorded with the restart", "no reason is recorded"))
	cmd.Flags().Float64Var(&timeout, "timeout", 300,
		"reconnect wait limit in seconds; 0 waits indefinitely")
	cmd.Flags().Float64Var(&interval, "interval", 5,
		"seconds between health polls; 0 polls without sleeping")
	return cmd
}

func (a *app) waitReconnectCmd() *cobra.Command {
	var retryTask, sinceBoot string
	var timeout, interval float64
	cmd := &cobra.Command{
		Use:   "wait-reconnect",
		Short: "Wait for the extension to become healthy",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			if _, err := waitHealth(a.client, sinceBoot, timeout, interval); err != nil {
				return a.result(fail(err, a.globals.JSON, 5))
			}
			if retryTask != "" {
				task, err := retryVideo(a.client, retryTask)
				if err != nil {
					return a.result(fail(err, a.globals.JSON, 1))
				}
				if a.globals.JSON {
					return a.result(emitJSON(task))
				}
				printStatus(task)
				return nil
			}
			if a.globals.JSON {
				return a.result(emitJSON(map[string]any{"status": "connected"}))
			}
			fmt.Println("connected")
			return nil
		},
	}
	cmd.Flags().StringVar(&retryTask, "retry-task", "",
		optional("retry this blocked video task after reconnect", "no task is retried"))
	cmd.Flags().StringVar(&sinceBoot, "since-boot", "",
		optional("require a boot_id different from this value", "accept the first healthy response"))
	cmd.Flags().Float64Var(&timeout, "timeout", 300,
		"reconnect wait limit in seconds; 0 waits indefinitely")
	cmd.Flags().Float64Var(&interval, "interval", 5,
		"seconds between health polls; 0 polls without sleeping")
	return cmd
}

func (a *app) downloadsCmd() *cobra.Command {
	parent := &cobra.Command{
		Use:   "downloads",
		Short: "Manage model downloads",
		Long:  "Manage model downloads. With no subcommand, behaves like `downloads list`.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.downloadsList("all"))
		},
	}
	parent.AddCommand(
		a.downloadsListCmd(),
		a.downloadsCreateCmd(),
		a.downloadsStatusCmd(),
		a.downloadsCancelCmd(),
	)
	return parent
}

func (a *app) downloadsListCmd() *cobra.Command {
	var status string
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List download tasks",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.downloadsList(status))
		},
	}
	cmd.Flags().StringVar(&status, "status", "all",
		"status filter: all, queued, running, completed, failed, cancelled")
	return cmd
}

func (a *app) downloadsList(status string) int {
	q := url.Values{"status": []string{status}}
	m, err := a.client.getJSON("/v2/extension/model/download", q)
	if err != nil {
		return fail(err, a.globals.JSON, 1)
	}
	if a.globals.JSON {
		return emitJSON(m)
	}
	if tasks, ok := m["tasks"].([]any); ok {
		for _, item := range tasks {
			task, _ := item.(map[string]any)
			fmt.Printf("%-40v %-12v %v\n", task["task_id"], task["status"], task["url"])
		}
	}
	return 0
}

func (a *app) downloadsCreateCmd() *cobra.Command {
	var folder, filename string
	cmd := &cobra.Command{
		Use:   "create URL",
		Short: "Queue a model download",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			payload := map[string]any{"url": args[0]}
			if folder != "" {
				payload["folder"] = folder
			}
			if filename != "" {
				payload["filename"] = filename
			}
			m, err := a.client.postJSON("/v2/extension/model/download", payload)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Printf("download task %v queued\n", m["task_id"])
			return nil
		},
	}
	cmd.Flags().StringVar(&folder, "folder", "",
		optional("destination model folder", "the server default (checkpoints)"))
	cmd.Flags().StringVar(&filename, "filename", "",
		optional("destination filename", "derive it from the URL on the server"))
	return cmd
}

func (a *app) downloadsStatusCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "status ID",
		Short: "Show a download task",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.getJSON("/v2/extension/model/download/"+url.PathEscape(args[0]), nil)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			for _, k := range []string{"task_id", "status", "progress", "url", "local_path", "error"} {
				if v, ok := m[k]; ok && v != nil {
					fmt.Printf("%-12s %v\n", k, v)
				}
			}
			return nil
		},
	}
}

func (a *app) downloadsCancelCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "cancel ID",
		Short: "Cancel a download task",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.deleteJSON("/v2/extension/model/download/" + url.PathEscape(args[0]))
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("cancelled:", m["task_id"])
			return nil
		},
	}
}

func (a *app) modelsCmd() *cobra.Command {
	parent := &cobra.Command{
		Use:   "models",
		Short: "List and inspect models",
		Long:  "List and inspect models. With no subcommand, behaves like `models list`.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.modelsList(""))
		},
	}
	parent.AddCommand(
		a.modelsListCmd(),
		a.modelsGetCmd(),
		a.modelsAllCmd(),
		a.modelsInfoCmd(),
		a.modelsDeleteCmd(),
	)
	return parent
}

func (a *app) modelsListCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List available model ids",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.modelsList(""))
		},
	}
}

func (a *app) modelsList(id string) int {
	path := "/v1/models"
	if id != "" {
		path += "/" + url.PathEscape(id)
	}
	m, err := a.client.getJSON(path, nil)
	if err != nil {
		return fail(err, a.globals.JSON, 1)
	}
	if a.globals.JSON {
		return emitJSON(m)
	}
	if id != "" {
		fmt.Println(m["id"], m["modality"])
		return 0
	}
	if items, ok := m["data"].([]any); ok {
		for _, item := range items {
			model, _ := item.(map[string]any)
			fmt.Printf("%-28v %v\n", model["id"], model["modality"])
		}
	}
	return 0
}

func (a *app) modelsGetCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "get ID",
		Short: "Show one model card",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.modelsList(args[0]))
		},
	}
}

func (a *app) modelsAllCmd() *cobra.Command {
	var folder string
	var hash, metadata bool
	cmd := &cobra.Command{
		Use:   "all",
		Short: "List model files on disk",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			q := url.Values{}
			if folder != "" {
				q.Set("folder", folder)
			}
			q.Set("include_hash", strconv.FormatBool(hash))
			q.Set("include_metadata", strconv.FormatBool(metadata))
			m, err := a.client.getJSON("/v2/extension/models/all", q)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("total:", m["total"])
			for _, item := range anySlice(m["models"]) {
				obj, _ := item.(map[string]any)
				fmt.Printf("%-16v %-60v %v\n", obj["folder"], obj["path"], obj["size"])
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&folder, "folder", "",
		optional("folder filter", "all folders are included"))
	cmd.Flags().BoolVar(&hash, "hash", false,
		optional("include file hashes", "hashes are not included"))
	cmd.Flags().BoolVar(&metadata, "metadata", false,
		optional("include file metadata", "metadata is not included"))
	return cmd
}

func (a *app) modelsInfoCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "info PATH",
		Short: "Inspect a model file",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.getJSON("/v2/extension/models/info", url.Values{"path": []string{args[0]}})
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			for _, k := range []string{"name", "path", "full_path", "size", "hash", "created", "modified"} {
				if v, ok := m[k]; ok && v != nil {
					fmt.Printf("%-12s %v\n", k, v)
				}
			}
			return nil
		},
	}
}

func (a *app) modelsDeleteCmd() *cobra.Command {
	var force bool
	cmd := &cobra.Command{
		Use:   "delete PATH",
		Short: "Delete a model file",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			path := "/v2/extension/models/" + escapePath(args[0])
			if force {
				path += "?force=true"
			}
			m, err := a.client.deleteJSON(path)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("deleted:", m["deleted"])
			return nil
		},
	}
	cmd.Flags().BoolVar(&force, "force", false,
		optional("skip protected-model checks", "protected models are refused"))
	return cmd
}

func (a *app) videosCmd() *cobra.Command {
	parent := &cobra.Command{
		Use:   "videos",
		Short: "Generate and manage video tasks",
		Long:  "Generate and manage video tasks. With no subcommand, behaves like `videos list`.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.videosList())
		},
	}
	parent.AddCommand(
		a.videoGenerateCmd(),
		a.videosListCmd(),
		a.videosStatusCmd(),
		a.videosWaitCmd(),
		a.videosContentCmd(),
		a.videosRetryCmd(),
		a.videosDeleteCmd(),
	)
	return parent
}

func (a *app) videosListCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List video tasks",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.videosList())
		},
	}
}

func (a *app) videosList() int {
	m, err := a.client.getJSON("/v1/videos", nil)
	if err != nil {
		return fail(err, a.globals.JSON, 1)
	}
	if a.globals.JSON {
		return emitJSON(m)
	}
	if items, ok := m["data"].([]any); ok {
		for _, item := range items {
			task, _ := item.(map[string]any)
			fmt.Printf("%-42v %v\n", task["id"], task["status"])
		}
	}
	return 0
}

func (a *app) videoGenerateCmd() *cobra.Command {
	var model, speedJSON, format, codec string
	var width, height, seed int
	var seconds float64
	var images, videos []string
	cmd := &cobra.Command{
		Use:   "generate PROMPT",
		Short: "Generate a video asynchronously",
		Long:  "Generate a video asynchronously. The command returns a task id; use `videos wait` and `videos content` to retrieve the file.",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.generateVideo(args[0], videoGenerateOptions{
				model: model, width: width, height: height, seconds: seconds,
				seed: int64(seed), speedJSON: speedJSON, format: format, codec: codec,
				images: images, videos: videos,
			}))
		},
	}
	cmd.Flags().StringVar(&model, "model", "",
		optional("video model id", "profile default_model, then the server default"))
	cmd.Flags().IntVar(&width, "width", 0,
		optional("frame width in pixels", "the model default (H3: 1344)"))
	cmd.Flags().IntVar(&height, "height", 0,
		optional("frame height in pixels", "the model default (H3: 768)"))
	cmd.Flags().Float64Var(&seconds, "seconds", 0,
		optional("video length in seconds", "the model default (H3: 5.0, snapped to its frame grid)"))
	cmd.Flags().IntVar(&seed, "seed", 0,
		optional("generation seed", "the model default (H3: 0)"))
	cmd.Flags().StringVar(&speedJSON, "speed-json", "",
		optional("structured acceleration JSON, or @FILE", "no acceleration override is sent"))
	cmd.Flags().StringVar(&format, "format", "auto",
		"output container: auto, mp4, mkv, webm; auto uses SaveVideo's choice")
	cmd.Flags().StringVar(&codec, "codec", "auto",
		"output codec: auto, h264, av1; WebP is not a video container")
	cmd.Flags().StringArrayVar(&images, "image", nil,
		optional("reference image path; repeatable", "no image reference; a single image becomes first_frame"))
	cmd.Flags().StringArrayVar(&videos, "video", nil,
		optional("reference video path; repeatable", "no video reference"))
	return cmd
}

type videoGenerateOptions struct {
	model     string
	width     int
	height    int
	seconds   float64
	seed      int64
	speedJSON string
	format    string
	codec     string
	images    []string
	videos    []string
}

func (a *app) generateVideo(prompt string, opts videoGenerateOptions) int {
	modelID := opts.model
	if modelID == "" {
		modelID = a.defaultModel
	}
	payload := map[string]any{"prompt": prompt}
	if modelID != "" {
		payload["model"] = modelID
	}
	if opts.width > 0 {
		payload["width"] = opts.width
	}
	if opts.height > 0 {
		payload["height"] = opts.height
	}
	if opts.seconds > 0 {
		payload["seconds"] = opts.seconds
	}
	if opts.seed != 0 {
		payload["seed"] = opts.seed
	}
	if opts.speedJSON != "" {
		speed, err := readSpeed(opts.speedJSON)
		if err != nil {
			return fail(err, a.globals.JSON, 2)
		}
		payload["speed"] = speed
	}
	videoFormat := strings.ToLower(opts.format)
	if videoFormat != "auto" && videoFormat != "mp4" && videoFormat != "mkv" && videoFormat != "webm" {
		return fail(fmt.Errorf("format must be auto, mp4, mkv, or webm"), a.globals.JSON, 2)
	}
	videoCodec := strings.ToLower(opts.codec)
	if videoCodec != "auto" && videoCodec != "h264" && videoCodec != "av1" {
		return fail(fmt.Errorf("codec must be auto, h264, or av1"), a.globals.JSON, 2)
	}
	payload["format"] = videoFormat
	payload["codec"] = videoCodec
	if len(opts.images) > 0 || len(opts.videos) > 0 {
		items := []any{map[string]any{"type": "input_text", "text": prompt}}
		role := "first_frame"
		if len(opts.images) > 1 || len(opts.videos) > 0 || strings.HasSuffix(modelID, "-r2v") {
			role = "reference_image"
		}
		for _, path := range opts.images {
			data, err := os.ReadFile(path)
			if err != nil {
				return fail(err, a.globals.JSON, 2)
			}
			ext := strings.TrimPrefix(strings.ToLower(filepath.Ext(path)), ".")
			if ext == "" {
				ext = "png"
			}
			items = append(items, map[string]any{
				"type":      "input_image",
				"image_url": "data:image/" + ext + ";base64," + base64.StdEncoding.EncodeToString(data),
				"role":      role,
			})
		}
		for _, path := range opts.videos {
			data, err := os.ReadFile(path)
			if err != nil {
				return fail(err, a.globals.JSON, 2)
			}
			ext := strings.TrimPrefix(strings.ToLower(filepath.Ext(path)), ".")
			if ext == "" {
				ext = "mp4"
			}
			items = append(items, map[string]any{
				"type":      "input_video",
				"video_url": "data:video/" + ext + ";base64," + base64.StdEncoding.EncodeToString(data),
			})
		}
		payload["input"] = items
		delete(payload, "prompt")
	}
	m, err := a.client.postJSON("/v1/videos", payload)
	if err != nil {
		return fail(err, a.globals.JSON, 1)
	}
	if a.globals.JSON {
		return emitJSON(m)
	}
	fmt.Printf("task %v queued (status: %v)\n", m["id"], m["status"])
	return 0
}

func (a *app) videosStatusCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "status ID",
		Short: "Show a video task",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.getJSON("/v1/videos/"+url.PathEscape(args[0]), nil)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			printStatus(m)
			return nil
		},
	}
}

func (a *app) videosWaitCmd() *cobra.Command {
	var timeout, interval float64
	cmd := &cobra.Command{
		Use:   "wait ID",
		Short: "Wait for a video task to finish",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			id := args[0]
			deadline := time.Time{}
			if timeout > 0 {
				deadline = time.Now().Add(time.Duration(timeout * float64(time.Second)))
			}
			for {
				m, err := a.client.getJSON("/v1/videos/"+url.PathEscape(id), nil)
				if err != nil {
					return a.result(fail(err, a.globals.JSON, 1))
				}
				status := fmt.Sprint(m["status"])
				if !a.globals.JSON {
					fmt.Printf("%s %v%%\n", status, m["progress"])
				}
				if status == "completed" {
					if a.globals.JSON {
						return a.result(emitJSON(m))
					}
					printStatus(m)
					return nil
				}
				if status == "failed" || status == "cancelled" {
					if a.globals.JSON {
						return a.result(emitJSON(m))
					}
					printStatus(m)
					return a.result(1)
				}
				if !deadline.IsZero() && time.Now().After(deadline) {
					return a.result(fail(fmt.Errorf("timed out waiting for %s", id), a.globals.JSON, 5))
				}
				if interval > 0 {
					time.Sleep(time.Duration(interval * float64(time.Second)))
				}
			}
		},
	}
	cmd.Flags().Float64Var(&timeout, "timeout", 300,
		"maximum seconds to wait; 0 waits indefinitely")
	cmd.Flags().Float64Var(&interval, "interval", 5,
		"seconds between polls; 0 polls without sleeping")
	return cmd
}

func (a *app) videosContentCmd() *cobra.Command {
	var out string
	cmd := &cobra.Command{
		Use:   "content ID",
		Short: "Download a completed video",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			id := args[0]
			path := out
			if path == "" {
				ext := ".mp4"
				if task, err := a.client.getJSON("/v1/videos/"+url.PathEscape(id), nil); err == nil {
					switch fmt.Sprint(task["media_type"]) {
					case "video/webm":
						ext = ".webm"
					case "video/x-matroska":
						ext = ".mkv"
					}
				}
				path = id + ext
			}
			data, err := a.client.getRaw("/v1/videos/" + url.PathEscape(id) + "/content")
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if err := os.WriteFile(path, data, 0o644); err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(map[string]any{"path": path, "bytes": len(data)}))
			}
			fmt.Println("saved:", filepath.Clean(path))
			return nil
		},
	}
	cmd.Flags().StringVar(&out, "out", "",
		optional("output path", "derive the suffix from the task media type (ID.mp4/ID.webm/ID.mkv)"))
	return cmd
}

func (a *app) videosRetryCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "retry ID",
		Short: "Retry a provisioning-blocked video task",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.postJSON("/v1/videos/"+url.PathEscape(args[0])+"/retry", map[string]any{})
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			printStatus(m)
			return nil
		},
	}
}

func (a *app) videosDeleteCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "delete ID",
		Short: "Delete a video task record",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.deleteJSON("/v1/videos/" + url.PathEscape(args[0]))
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("deleted:", args[0])
			return nil
		},
	}
}

func (a *app) imagesCmd() *cobra.Command {
	parent := &cobra.Command{
		Use:   "images",
		Short: "Generate and manage image tasks",
		Long:  "Generate and manage image tasks. With no subcommand, behaves like `images list`.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.imagesList())
		},
	}
	parent.AddCommand(
		a.imageGenerateCmd(false),
		a.imageGenerateCmd(true),
		a.imagesListCmd(),
		a.imagesStatusCmd(),
		a.imagesContentCmd(),
	)
	return parent
}

func (a *app) imagesListCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List image tasks",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.imagesList())
		},
	}
}

func (a *app) imagesList() int {
	m, err := a.client.getJSON("/v2/extension/image", nil)
	if err != nil {
		return fail(err, a.globals.JSON, 1)
	}
	if a.globals.JSON {
		return emitJSON(m)
	}
	if items, ok := m["tasks"].([]any); ok {
		for _, item := range items {
			task, _ := item.(map[string]any)
			fmt.Printf("%-42v %v\n", task["id"], task["status"])
		}
	}
	return 0
}

func (a *app) imageGenerateCmd(edit bool) *cobra.Command {
	var model, size, out string
	var width, height, n, seed int
	var images []string
	use := "generate PROMPT"
	short := "Generate an image synchronously"
	cmd := &cobra.Command{
		Use:   use,
		Short: short,
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.generateImage(args[0], imageOptions{
				model: model, size: size, width: width, height: height,
				n: n, seed: int64(seed), out: out, images: images, edit: edit,
			}))
		},
	}
	if edit {
		cmd.Use = "edit PROMPT"
		cmd.Short = "Edit an image synchronously"
	}
	cmd.Flags().StringVar(&model, "model", "",
		optional("image model id", "profile default_model, then the server default"))
	cmd.Flags().StringVar(&size, "size", "",
		optional("output size WIDTHxHEIGHT or auto", "the model default"))
	cmd.Flags().IntVar(&width, "width", 0,
		optional("output width in pixels", "the model default"))
	cmd.Flags().IntVar(&height, "height", 0,
		optional("output height in pixels", "the model default"))
	cmd.Flags().IntVar(&n, "n", 0,
		optional("number of images", "the model default"))
	cmd.Flags().IntVar(&seed, "seed", 0,
		optional("generation seed", "the model default"))
	cmd.Flags().StringVar(&out, "out", "image.png",
		"path for the first image; with --n > 1 later files get _2, _3; the extension does not transcode")
	if edit {
		cmd.Flags().StringArrayVar(&images, "image", nil,
			"reference image path; repeatable (required)")
		_ = cmd.MarkFlagRequired("image")
	}
	return cmd
}

type imageOptions struct {
	model  string
	size   string
	width  int
	height int
	n      int
	seed   int64
	out    string
	images []string
	edit   bool
}

func (a *app) generateImage(prompt string, opts imageOptions) int {
	modelID := opts.model
	if modelID == "" {
		modelID = a.defaultModel
	}
	payload := map[string]any{"prompt": prompt}
	if modelID != "" {
		payload["model"] = modelID
	}
	if opts.size != "" {
		payload["size"] = opts.size
	}
	if opts.width > 0 {
		payload["width"] = opts.width
	}
	if opts.height > 0 {
		payload["height"] = opts.height
	}
	if opts.n > 0 {
		payload["n"] = opts.n
	}
	if opts.seed != 0 {
		payload["seed"] = opts.seed
	}
	var data []byte
	var reqErr error
	if opts.edit {
		if len(opts.images) == 0 {
			return fail(fmt.Errorf("images edit requires --image"), a.globals.JSON, 2)
		}
		var body bytes.Buffer
		writer := multipart.NewWriter(&body)
		for key, value := range payload {
			_ = writer.WriteField(key, fmt.Sprint(value))
		}
		for i, path := range opts.images {
			file, e := os.Open(path)
			if e != nil {
				return fail(e, a.globals.JSON, 2)
			}
			part, e := writer.CreateFormFile("image", fmt.Sprintf("image%d%s", i+1, filepath.Ext(path)))
			if e != nil {
				file.Close()
				return fail(e, a.globals.JSON, 1)
			}
			_, e = io.Copy(part, file)
			file.Close()
			if e != nil {
				return fail(e, a.globals.JSON, 1)
			}
		}
		if err := writer.Close(); err != nil {
			return fail(err, a.globals.JSON, 1)
		}
		data, _, reqErr = a.client.request("POST", "/v1/images/edits", body.Bytes(), writer.FormDataContentType(), false)
	} else {
		data, _, reqErr = a.client.request("POST", "/v1/images/generations", mustJSON(payload), "application/json", false)
	}
	if reqErr != nil {
		return fail(reqErr, a.globals.JSON, 1)
	}
	var response map[string]any
	if err := json.Unmarshal(data, &response); err != nil {
		return fail(err, a.globals.JSON, 1)
	}
	items, _ := response["data"].([]any)
	saved := make([]string, 0, len(items))
	for i, item := range items {
		obj, _ := item.(map[string]any)
		if b64, ok := obj["b64_json"].(string); ok {
			raw, err := base64.StdEncoding.DecodeString(b64)
			if err != nil {
				return fail(err, a.globals.JSON, 1)
			}
			path := opts.out
			if i > 0 {
				ext := filepath.Ext(path)
				if ext == "" {
					ext = ".png"
				}
				path = strings.TrimSuffix(path, ext) + "_" + strconv.Itoa(i+1) + ext
			}
			if err := os.WriteFile(path, raw, 0o644); err != nil {
				return fail(err, a.globals.JSON, 1)
			}
			saved = append(saved, path)
			if !a.globals.JSON {
				fmt.Println("saved:", path)
			}
		}
	}
	if a.globals.JSON {
		response["saved_paths"] = saved
		return emitJSON(response)
	}
	return 0
}

func (a *app) imagesStatusCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "status ID",
		Short: "Show an image task",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.getJSON("/v2/extension/image/"+url.PathEscape(args[0]), nil)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			printStatus(m)
			return nil
		},
	}
}

func (a *app) imagesContentCmd() *cobra.Command {
	var out string
	var index int
	cmd := &cobra.Command{
		Use:   "content ID",
		Short: "Download one image output",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			id := args[0]
			path := out
			if path == "" {
				path = fmt.Sprintf("%s_%d.png", id, index)
			}
			q := url.Values{"index": []string{strconv.Itoa(index)}}
			data, err := a.client.getRaw("/v2/extension/image/" + url.PathEscape(id) + "/content?" + q.Encode())
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if err := os.WriteFile(path, data, 0o644); err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(map[string]any{"path": path, "bytes": len(data)}))
			}
			fmt.Println("saved:", filepath.Clean(path))
			return nil
		},
	}
	cmd.Flags().StringVar(&out, "out", "",
		optional("output path", "save as ID_INDEX.png"))
	cmd.Flags().IntVar(&index, "index", 0,
		optional("output index", "0"))
	return cmd
}

func (a *app) depsCmd() *cobra.Command {
	parent := &cobra.Command{
		Use:   "deps",
		Short: "Inspect and install workflow dependencies",
		Long:  "Inspect and install workflow dependencies. A subcommand is required.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.helpExit(cmd)
		},
	}
	parent.AddCommand(
		a.depsWorkflowCmd("check-workflow", false),
		a.depsWorkflowCmd("install", true),
		a.depsStatusCmd(),
		a.depsCheckNodeCmd(),
		a.depsRestoreCmd(),
	)
	return parent
}

func (a *app) depsWorkflowCmd(name string, install bool) *cobra.Command {
	var workflow string
	var sync bool
	short := "Check workflow dependency status"
	if install {
		short = "Install workflow dependencies"
	}
	cmd := &cobra.Command{
		Use:   name,
		Short: short,
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			wf, err := readWorkflow(workflow)
			if err != nil {
				return a.result(fail(fmt.Errorf("workflow error: %w", err), a.globals.JSON, 2))
			}
			var m map[string]any
			if !install {
				m, err = a.client.postJSON("/v2/extension/workflow/dependencies/check", map[string]any{"workflow": wf})
			} else {
				m, err = a.client.postJSON("/v2/extension/workflow/dependencies", map[string]any{
					"workflow": wf, "async_install": !sync,
				})
			}
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if install && sync {
				id := fmt.Sprint(m["task_id"])
				deadline := time.Now().Add(300 * time.Second)
				for fmt.Sprint(m["status"]) != "completed" && fmt.Sprint(m["status"]) != "failed" &&
					fmt.Sprint(m["status"]) != "cancelled" && time.Now().Before(deadline) {
					time.Sleep(2 * time.Second)
					m, err = a.client.getJSON("/v2/extension/workflow/dependencies/"+url.PathEscape(id), nil)
					if err != nil {
						return a.result(fail(err, a.globals.JSON, 1))
					}
				}
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("status:", m["status"])
			return nil
		},
	}
	cmd.Flags().StringVar(&workflow, "workflow", "",
		"workflow JSON path, or - for stdin (required)")
	_ = cmd.MarkFlagRequired("workflow")
	if install {
		cmd.Flags().BoolVar(&sync, "sync", false,
			optional("wait for installation to finish", "return the install task immediately"))
	}
	return cmd
}

func (a *app) depsStatusCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "status ID",
		Short: "Show a dependency install task",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.getJSON("/v2/extension/workflow/dependencies/"+url.PathEscape(args[0]), nil)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			for k, v := range m {
				if v != nil {
					fmt.Printf("%-16s %v\n", k, v)
				}
			}
			return nil
		},
	}
}

func (a *app) depsCheckNodeCmd() *cobra.Command {
	var node string
	cmd := &cobra.Command{
		Use:   "check-node",
		Short: "Check one node's dependency status",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.getJSON("/v2/extension/dependencies/check", url.Values{"node": []string{node}})
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("node:", m["node"])
			return nil
		},
	}
	cmd.Flags().StringVar(&node, "node", "",
		"node class name (required)")
	_ = cmd.MarkFlagRequired("node")
	return cmd
}

func (a *app) depsRestoreCmd() *cobra.Command {
	var nodes []string
	var sync bool
	cmd := &cobra.Command{
		Use:   "restore",
		Short: "Restore dependencies for one or more nodes",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.postJSON("/v2/extension/dependencies/restore", map[string]any{
				"nodes": nodes, "async_mode": !sync,
			})
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("status:", m["status"])
			return nil
		},
	}
	cmd.Flags().StringArrayVar(&nodes, "node", nil,
		"node name; repeatable (required)")
	_ = cmd.MarkFlagRequired("node")
	cmd.Flags().BoolVar(&sync, "sync", false,
		optional("wait for restoration to finish", "return the restore task immediately"))
	return cmd
}

func (a *app) snapshotCmd() *cobra.Command {
	parent := &cobra.Command{
		Use:   "snapshot",
		Short: "Export, import, diff, and list snapshots",
		Long:  "Export, import, diff, and list snapshots. A subcommand is required.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.helpExit(cmd)
		},
	}
	parent.AddCommand(
		a.snapshotExportCmd(),
		a.snapshotImportCmd(),
		a.snapshotDiffCmd(),
		a.snapshotListCmd(),
	)
	return parent
}

func (a *app) snapshotExportCmd() *cobra.Command {
	var format string
	var includeModels bool
	cmd := &cobra.Command{
		Use:   "export SNAPSHOT",
		Short: "Export a snapshot",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.postJSON("/v2/extension/snapshot/export", map[string]any{
				"snapshot_id": args[0], "format": format, "include_models": includeModels,
			})
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("success:", m["success"], "path:", m["path"])
			return nil
		},
	}
	cmd.Flags().StringVar(&format, "format", "tarball",
		"archive format: tarball or zip")
	cmd.Flags().BoolVar(&includeModels, "include-models", false,
		optional("include model files in the archive", "model files are excluded"))
	return cmd
}

func (a *app) snapshotImportCmd() *cobra.Command {
	var noModels, noNodes bool
	cmd := &cobra.Command{
		Use:   "import PATH",
		Short: "Import a snapshot",
		Long:  "Import the snapshot archive at server-side PATH. Models and custom nodes are restored unless the corresponding --no-restore-* flag is set.",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.postJSON("/v2/extension/snapshot/import", map[string]any{
				"path": args[0], "restore_models": !noModels, "restore_nodes": !noNodes,
			})
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("success:", m["success"])
			return nil
		},
	}
	cmd.Flags().BoolVar(&noModels, "no-restore-models", false,
		optional("skip model restoration", "models are restored"))
	cmd.Flags().BoolVar(&noNodes, "no-restore-nodes", false,
		optional("skip custom-node restoration", "custom nodes are restored"))
	return cmd
}

func (a *app) snapshotDiffCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "diff A B",
		Short: "Diff two snapshots",
		Long:  "Compare snapshots A and B by custom nodes and pip packages.",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.getJSON("/v2/extension/snapshot/diff", url.Values{
				"snapshot_a": []string{args[0]}, "snapshot_b": []string{args[1]},
			})
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			for _, k := range []string{"custom_nodes", "pip_packages"} {
				fmt.Println(k + ":")
				if section, ok := m[k].(map[string]any); ok {
					for _, change := range []string{"added", "removed", "common"} {
						fmt.Printf("  %s: %v\n", change, section[change])
					}
				}
			}
			return nil
		},
	}
}

func (a *app) snapshotListCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List snapshots",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.getJSON("/v2/extension/snapshot/list", nil)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			for _, item := range anySlice(m["snapshots"]) {
				obj, _ := item.(map[string]any)
				fmt.Printf("%v %v %v\n", obj["name"], obj["size"], obj["path"])
			}
			return nil
		},
	}
}

func (a *app) nodesCmd() *cobra.Command {
	parent := &cobra.Command{
		Use:   "nodes",
		Short: "List, pack, validate, and initialize custom nodes",
		Long:  "List, pack, validate, and initialize custom nodes. A subcommand is required.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.helpExit(cmd)
		},
	}
	parent.AddCommand(
		a.nodesListCmd(),
		a.nodesPackCmd(),
		a.nodesValidateCmd(),
		a.nodesInitCmd(),
	)
	return parent
}

func (a *app) nodesListCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List custom nodes",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.getJSON("/v2/extension/nodes/list", nil)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			for _, item := range anySlice(m["nodes"]) {
				obj, _ := item.(map[string]any)
				fmt.Printf("%-40v git=%v init=%v %v\n", obj["name"], obj["is_git"], obj["has_init"], obj["path"])
			}
			return nil
		},
	}
}

func (a *app) nodesPackCmd() *cobra.Command {
	var noIgnore bool
	cmd := &cobra.Command{
		Use:   "pack NAME",
		Short: "Pack a custom node into a zip",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.postJSON("/v2/extension/nodes/pack", map[string]any{
				"node_name": args[0], "respect_comfyignore": !noIgnore,
			})
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("path:", m["path"], "size:", m["size"])
			return nil
		},
	}
	cmd.Flags().BoolVar(&noIgnore, "no-comfyignore", false,
		optional("ignore .comfyignore rules", ".comfyignore is respected"))
	return cmd
}

func (a *app) nodesValidateCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "validate NAME",
		Short: "Validate a custom node",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.postJSON("/v2/extension/nodes/validate", map[string]any{"node_name": args[0]})
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("valid:", m["valid"])
			if m["valid"] == false {
				return a.result(1)
			}
			return nil
		},
	}
}

func (a *app) nodesInitCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "init PATH",
		Short: "Initialize a custom node project",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.postJSON("/v2/extension/nodes/init", map[string]any{"path": args[0]})
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("path:", m["path"])
			return nil
		},
	}
}

func (a *app) prCacheCmd() *cobra.Command {
	parent := &cobra.Command{
		Use:   "pr-cache",
		Short: "Inspect and clear the frontend PR cache",
		Long:  "Inspect and clear the frontend PR cache. With no subcommand, behaves like `pr-cache list`.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.prCacheList())
		},
	}
	parent.AddCommand(
		a.prCacheListCmd(),
		a.prCacheSizeCmd(),
		a.prCacheDeleteCmd(),
		a.prCacheClearCmd(),
	)
	return parent
}

func (a *app) prCacheListCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List cached PR entries",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.result(a.prCacheList())
		},
	}
}

func (a *app) prCacheList() int {
	m, err := a.client.getJSON("/v2/extension/frontend/pr-cache", nil)
	if err != nil {
		return fail(err, a.globals.JSON, 1)
	}
	if a.globals.JSON {
		return emitJSON(m)
	}
	fmt.Println("count:", m["count"], "total:", m["total_size"])
	return 0
}

func (a *app) prCacheSizeCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "size",
		Short: "Show PR cache size",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.getJSON("/v2/extension/frontend/pr-cache/size", nil)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("count:", m["count"], "total:", m["total_size"])
			return nil
		},
	}
}

func (a *app) prCacheDeleteCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "delete PR",
		Short: "Delete one cached PR entry",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := a.client.deleteJSON("/v2/extension/frontend/pr-cache/" + url.PathEscape(args[0]))
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("deleted:", m["deleted"])
			return nil
		},
	}
}

func (a *app) prCacheClearCmd() *cobra.Command {
	var confirm bool
	cmd := &cobra.Command{
		Use:   "clear",
		Short: "Clear the entire PR cache",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			if !confirm {
				return a.result(fail(fmt.Errorf("refusing to clear PR cache without --confirm"), a.globals.JSON, 2))
			}
			m, err := a.client.deleteJSON("/v2/extension/frontend/pr-cache?confirm=true")
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 1))
			}
			if a.globals.JSON {
				return a.result(emitJSON(m))
			}
			fmt.Println("cleared:", m["cleared"], "removed:", m["entries_removed"])
			return nil
		},
	}
	cmd.Flags().BoolVar(&confirm, "confirm", false,
		"confirm this destructive operation (required for clear)")
	_ = cmd.MarkFlagRequired("confirm")
	return cmd
}

func (a *app) configCmd() *cobra.Command {
	parent := &cobra.Command{
		Use:   "config",
		Short: "Manage CLI profiles",
		Long:  "Manage CLI profiles. A subcommand is required.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.helpExit(cmd)
		},
	}
	parent.AddCommand(
		a.configListCmd(),
		a.configUseCmd(),
		a.configSetCmd(),
		a.configGetCmd(),
		a.configPathCmd(),
	)
	return parent
}

func (a *app) configPathCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "path",
		Short: "Print the config file path",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			if a.globals.JSON {
				return a.result(emitJSON(map[string]any{"path": configPath()}))
			}
			fmt.Println(configPath())
			return nil
		},
	}
}

func (a *app) configListCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List configured profiles",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			if a.globals.JSON {
				return a.result(emitJSON(redactedConfig(a.cfg)))
			}
			fmt.Printf("default profile: %s\n", a.cfg.DefaultProfile)
			for _, name := range sortedKeys(a.cfg.Profiles) {
				p := a.cfg.Profiles[name]
				fmt.Printf("%s", name)
				var bits []string
				if p.BaseURL != "" {
					bits = append(bits, "base_url="+p.BaseURL)
				}
				if p.APIKey != "" {
					bits = append(bits, "api_key=***")
				}
				if p.Timeout != 0 {
					bits = append(bits, fmt.Sprintf("timeout=%g", p.Timeout))
				}
				if p.DefaultModel != "" {
					bits = append(bits, "default_model="+p.DefaultModel)
				}
				if len(bits) > 0 {
					fmt.Printf("  (%s)", strings.Join(bits, ", "))
				}
				fmt.Println()
			}
			if len(a.cfg.Profiles) == 0 {
				fmt.Println("(no profiles configured)")
			}
			return nil
		},
	}
}

func (a *app) configUseCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "use PROFILE",
		Short: "Select the default profile",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			if _, ok := a.cfg.Profiles[name]; !ok {
				return a.result(fail(fmt.Errorf("profile not found: %s", name), a.globals.JSON, 4))
			}
			a.cfg.DefaultProfile = name
			path, err := saveConfig(a.cfg)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 2))
			}
			if a.globals.JSON {
				return a.result(emitJSON(map[string]any{"default_profile": name, "path": path}))
			}
			fmt.Printf("default profile: %s\n", name)
			return nil
		},
	}
}

func (a *app) configSetCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "set PROFILE KEY VALUE",
		Short: "Set a profile field",
		Long:  "Set one field on PROFILE. KEY is base_url, api_key, timeout, or default_model. For api_key, VALUE '-' reads the key from stdin.",
		Args:  cobra.ExactArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			profileName, key, value := args[0], args[1], args[2]
			if !validProfileName(profileName) {
				return a.result(fail(fmt.Errorf("invalid profile name: %s", profileName), a.globals.JSON, 2))
			}
			if !configFields[key] {
				return a.result(fail(fmt.Errorf("unknown config key: %s", key), a.globals.JSON, 2))
			}
			if key == "api_key" && value == "-" {
				data, err := io.ReadAll(os.Stdin)
				if err != nil {
					return a.result(fail(err, a.globals.JSON, 1))
				}
				value = strings.TrimRight(string(data), "\r\n")
			}
			p := a.cfg.Profiles[profileName]
			switch key {
			case "base_url":
				p.BaseURL = value
			case "api_key":
				p.APIKey = value
			case "timeout":
				f, err := strconv.ParseFloat(value, 64)
				if err != nil {
					return a.result(fail(fmt.Errorf("timeout must be a number"), a.globals.JSON, 2))
				}
				p.Timeout = f
			case "default_model":
				p.DefaultModel = value
			}
			a.cfg.Profiles[profileName] = p
			path, err := saveConfig(a.cfg)
			if err != nil {
				return a.result(fail(err, a.globals.JSON, 2))
			}
			if a.globals.JSON {
				return a.result(emitJSON(map[string]any{"profile": profileName, "key": key, "path": path}))
			}
			fmt.Printf("set %s for profile %s\n", key, profileName)
			return nil
		},
	}
}

func (a *app) configGetCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "get PROFILE [KEY]",
		Short: "Show profile fields",
		Long:  "Show all fields for PROFILE, or only KEY when supplied.",
		Args:  cobra.RangeArgs(1, 2),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := args[0]
			p, ok := a.cfg.Profiles[name]
			if !ok {
				return a.result(fail(fmt.Errorf("profile not found: %s", name), a.globals.JSON, 4))
			}
			values := profileMap(p)
			if len(args) == 2 {
				key := args[1]
				v, ok := values[key]
				if !ok {
					return a.result(fail(fmt.Errorf("key not set: %s", key), a.globals.JSON, 4))
				}
				if key == "api_key" && v != "" {
					v = "***"
				}
				if a.globals.JSON {
					return a.result(emitJSON(map[string]any{"profile": name, "key": key, "value": v}))
				}
				fmt.Println(v)
				return nil
			}
			if a.globals.JSON {
				return a.result(emitJSON(values))
			}
			for _, k := range []string{"api_key", "base_url", "default_model", "timeout"} {
				if v, ok := values[k]; ok {
					fmt.Printf("%s=%v\n", k, v)
				}
			}
			return nil
		},
	}
}

func (a *app) versionCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Print the CLI version",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			if a.globals.JSON {
				return a.result(emitJSON(map[string]any{"version": version}))
			}
			fmt.Println(version)
			return nil
		},
	}
}
