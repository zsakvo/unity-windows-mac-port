// BuildDonor.cs - batch/GUI entry point for the "shader donor" Unity project.
//
// A Windows Unity build only contains D3D11 sub-programs for every shader, so a
// macOS player cannot use them.  This throw-away project contains the same
// shaders (Unity's built-ins come along automatically; package and custom
// shaders are added as sources), and building it for macOS makes Unity bake
// Metal sub-programs for all of them.  Those blobs are then copied into the
// game's serialized files by transplant_shaders.py / patch_game_data.py.
//
// Input:  needed-shaders.txt in the project root, one entry per line:
//             <Shader Name><TAB><keyword1 keyword2 ...>
//         (generate it with recon.py - it prints the (shader, keywords) pairs
//          the game's Material objects actually use)
//
// Usage:
//   Unity -projectPath <project> -executeMethod BuildDonor.BuildFromCommandLine -quit \
//         -logFile donor-build.log
//
// NOTE: run Unity WITHOUT -batchmode.  Batch mode takes the "headless"
// entitlement path, which on a Unity Personal seat fails the licence handshake
// ("Failed to activate/update license"), while the GUI path succeeds.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class BuildDonor
{
    struct Entry { public string shader; public string keywords; }

    static List<Entry> ReadNeeded()
    {
        var list = new List<Entry>();
        string path = "needed-shaders.txt";
        if (!File.Exists(path))
            throw new FileNotFoundException("needed-shaders.txt missing in the project root", path);
        foreach (var raw in File.ReadAllLines(path))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith("#")) continue;
            var parts = line.Split('\t');
            list.Add(new Entry { shader = parts[0].Trim(), keywords = parts.Length > 1 ? parts[1].Trim() : "" });
        }
        return list;
    }

    public static void BuildFromCommandLine()
    {
        try { Run(); EditorApplication.Exit(0); }
        catch (Exception e) { Debug.LogError("BuildDonor failed: " + e); EditorApplication.Exit(1); }
    }

    static void Run()
    {
        Directory.CreateDirectory("Assets/Scenes");
        Directory.CreateDirectory("Assets/Resources/Donor");

        // an empty scene is enough: materials live in Resources, shaders are force-included
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var camGo = new GameObject("Camera");
        camGo.AddComponent<Camera>().clearFlags = CameraClearFlags.SolidColor;
        SceneManager.MoveGameObjectToScene(camGo, scene);
        string scenePath = "Assets/Scenes/Donor.unity";
        EditorSceneManager.SaveScene(scene, scenePath);

        var needed = ReadNeeded();
        var shaders = new List<Shader>();
        int made = 0, missing = 0;
        foreach (var e in needed)
        {
            var sh = Shader.Find(e.shader);
            if (sh == null)
            {
                Debug.LogError("[donor] shader not found: " + e.shader);
                missing++;
                continue;
            }
            if (!shaders.Contains(sh)) shaders.Add(sh);

            string file = "Assets/Resources/Donor/" +
                          e.shader.Replace("/", "_").Replace(" ", "_").Replace("(", "").Replace(")", "") +
                          (e.keywords.Length == 0 ? "" : "_" + e.keywords.Replace(" ", "_")) + ".mat";
            if (File.Exists(file)) continue;
            var mat = new Material(sh);
            if (e.keywords.Length > 0) mat.shaderKeywords = e.keywords.Split(' ');
            AssetDatabase.CreateAsset(mat, file);
            made++;
        }
        Debug.Log($"[donor] materials created: {made}, distinct shaders: {shaders.Count}, missing: {missing}");

        // force every shader (and therefore every multi_compile variant) into the build
        var graphics = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/GraphicsSettings.asset")[0];
        var so = new SerializedObject(graphics);
        var arr = so.FindProperty("m_AlwaysIncludedShaders");
        var have = new HashSet<UnityEngine.Object>();
        for (int i = 0; i < arr.arraySize; i++) have.Add(arr.GetArrayElementAtIndex(i).objectReferenceValue);
        foreach (var sh in shaders)
        {
            if (have.Contains(sh)) continue;
            arr.InsertArrayElementAtIndex(arr.arraySize);
            arr.GetArrayElementAtIndex(arr.arraySize - 1).objectReferenceValue = sh;
        }
        so.ApplyModifiedProperties();
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
        Debug.Log($"[donor] always-included shaders now: {arr.arraySize}");

        string outDir = Path.GetFullPath(Path.Combine(Application.dataPath, "../../donor-build"));
        Directory.CreateDirectory(outDir);
        string outPath = Path.Combine(outDir, "Donor.app");

        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { scenePath },
            locationPathName = outPath,
            target = BuildTarget.StandaloneOSX,
            targetGroup = BuildTargetGroup.Standalone,
            options = BuildOptions.None,
        });
        Debug.Log($"[donor] build result={report.summary.result} size={report.summary.totalSize}");
        if (report.summary.result != UnityEditor.Build.Reporting.BuildResult.Succeeded)
            throw new Exception("BuildPlayer result " + report.summary.result);
    }
}
