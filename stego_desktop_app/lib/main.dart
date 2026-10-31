import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:file_picker/file_picker.dart';

class _ProcResult {
  final int exitCode;
  final String stdout;
  final String stderr;
  _ProcResult(this.exitCode, this.stdout, this.stderr);
}

void main() {
  runApp(const StegoApp());
}

class StegoApp extends StatelessWidget {
  const StegoApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Multi-Modal Steganography Ecosystem',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        primarySwatch: Colors.deepPurple,
        scaffoldBackgroundColor: const Color(0xFF121212),
        cardColor: const Color(0xFF1E1E1E),
      ),
      home: const MainDashboard(),
    );
  }
}

class MainDashboard extends StatefulWidget {
  const MainDashboard({super.key});

  @override
  State<MainDashboard> createState() => _MainDashboardState();
}

class _MainDashboardState extends State<MainDashboard> {
  int _selectedTab = 0;

  final _payloadController = TextEditingController();
  final _passwordController = TextEditingController();
  final _imgCoverController = TextEditingController();
  final _audCoverController = TextEditingController();
  final _txtCoverController = TextEditingController();

  final _magnetInputController = TextEditingController();
  final _receiverPasswordController = TextEditingController();
  
  // Controller for the generated magnet link so it can be fully selected with Ctrl+A
  final _magnetOutputController = TextEditingController();

  bool _obscureSenderPassword = true;
  bool _shareOnLan = false;
  bool _obscureReceiverPassword = true;

  bool _isLoading = false;
  String _statusMessage = "";
  String _receiverStatusMessage = "";

  @override
  void dispose() {
    _payloadController.dispose();
    _passwordController.dispose();
    _imgCoverController.dispose();
    _audCoverController.dispose();
    _txtCoverController.dispose();
    _magnetInputController.dispose();
    _receiverPasswordController.dispose();
    _magnetOutputController.dispose();
    super.dispose();
  }

  Future<void> _pickFile(TextEditingController controller, List<String> allowedExtensions) async {
    try {
      FilePickerResult? result = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: allowedExtensions,
      );

      if (result != null && result.files.single.path != null) {
        setState(() {
          controller.text = result.files.single.path!;
        });
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Error picking file: $e")),
      );
    }
  }

  String _apiPath() {
    final currentDir = Directory.current.path;
    return currentDir.endsWith('stego_desktop_app') ? r'..\backend_api.py' : 'backend_api.py';
  }

  // Runs the Python engine. The password is sent through standard input, not as a command-line
  // argument, so it does not appear in the process list.
  Future<_ProcResult> _runPython(List<String> args, {String stdinText = ''}) async {
    final process = await Process.start('python', args);
    final outFuture = process.stdout.transform(utf8.decoder).join();
    final errFuture = process.stderr.transform(utf8.decoder).join();
    try {
      if (stdinText.isNotEmpty) {
        process.stdin.add(utf8.encode('$stdinText\n'));
        await process.stdin.flush();
      }
      await process.stdin.close();
    } catch (_) {
      // The engine may already have exited; its output explains why.
    }
    final code = await process.exitCode;
    return _ProcResult(code, await outFuture, await errFuture);
  }

  void _stopSeeding() async {
    setState(() {
      _isLoading = true;
      _statusMessage = "Stopping the seeding nodes and withdrawing the carriers...";
    });
    try {
      final result = await _runPython([_apiPath(), '--stop']);
      if (result.exitCode == 0) {
        final outputLines = result.stdout.trim().split('\n');
        final data = jsonDecode(outputLines.last);
        setState(() {
          _statusMessage = data['message'];
          _magnetOutputController.clear();
        });
      } else {
        setState(() => _statusMessage = "Execution Error: ${result.stderr}");
      }
    } catch (e) {
      setState(() => _statusMessage = "Failed to stop the seeding nodes: $e");
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  void _runSenderPipeline() async {
    if (_payloadController.text.isEmpty ||
        _passwordController.text.isEmpty ||
        _imgCoverController.text.isEmpty ||
        _audCoverController.text.isEmpty ||
        _txtCoverController.text.isEmpty) {
      setState(() => _statusMessage = "Please fill in all fields and select all files!");
      return;
    }

    setState(() {
      _isLoading = true;
      _statusMessage = "Encrypting, splitting, and starting multi-port nodes...";
    });

    try {
      final result = await _runPython([
        _apiPath(),
        '--send',
        _payloadController.text,
        _imgCoverController.text,
        _audCoverController.text,
        _txtCoverController.text,
        if (_shareOnLan) '--lan',
      ], stdinText: _passwordController.text);

      if (result.exitCode == 0) {
        final outputLines = result.stdout.trim().split('\n');
        final jsonStr = outputLines.last;
        final data = jsonDecode(jsonStr);
        
        if (data['status'] == 'success') {
          setState(() {
            _magnetOutputController.text = data['magnet'];
            _statusMessage = data['message'];
          });
        } else {
          setState(() => _statusMessage = "Error: ${data['message']}");
        }
      } else {
        setState(() => _statusMessage = "Execution Error: ${result.stderr}");
      }
    } catch (e) {
      setState(() => _statusMessage = "Failed to launch Python engine: $e");
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  void _runReceiverPipeline() async {
    if (_magnetInputController.text.isEmpty || _receiverPasswordController.text.isEmpty) {
      setState(() => _receiverStatusMessage = "Please enter both the Magnet link and password!");
      return;
    }

    setState(() {
      _isLoading = true;
      _receiverStatusMessage = "Swarming 3 parallel nodes, verifying hashes, and decrypting...";
    });

    try {
      final result = await _runPython([
        _apiPath(),
        '--receive',
        _magnetInputController.text,
      ], stdinText: _receiverPasswordController.text);

      if (result.exitCode == 0) {
        final outputLines = result.stdout.trim().split('\n');
        final jsonStr = outputLines.last;
        final data = jsonDecode(jsonStr);
        
        setState(() {
          _receiverStatusMessage = data['message'];
        });
      } else {
        setState(() => _receiverStatusMessage = "Execution Error: ${result.stderr}");
      }
    } catch (e) {
      setState(() => _receiverStatusMessage = "Receiver error: $e");
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Multi-Modal Steganographic Privacy Ecosystem'),
        centerTitle: true,
        backgroundColor: const Color(0xFF1F1B24),
      ),
      body: Row(
        children: [
          NavigationRail(
            selectedIndex: _selectedTab,
            onDestinationSelected: (int index) {
              setState(() => _selectedTab = index);
            },
            labelType: NavigationRailLabelType.all,
            backgroundColor: const Color(0xFF1E1E1E),
            destinations: const [
              NavigationRailDestination(
                icon: Icon(Icons.lock),
                selectedIcon: Icon(Icons.lock_open),
                label: Text('Sender Node'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.download_rounded),
                selectedIcon: Icon(Icons.download_done_rounded),
                label: Text('Receiver Node'),
              ),
            ],
          ),
          const VerticalDivider(thickness: 1, width: 1),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.all(24.0),
              child: _selectedTab == 0 ? _buildSenderPanel() : _buildReceiverPanel(),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildSenderPanel() {
    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text("Phase 1 & 2: Lock, Split & Multi-Node Seeding",
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: Colors.deepPurpleAccent)),
          const SizedBox(height: 16),
          
          Row(
            children: [
              Expanded(child: TextField(controller: _payloadController, decoration: const InputDecoration(labelText: "Secret Payload File (.pdf, .docx, .txt)"))),
              const SizedBox(width: 12),
              ElevatedButton.icon(
                onPressed: () => _pickFile(_payloadController, ['pdf', 'docx', 'txt']),
                icon: const Icon(Icons.folder_open),
                label: const Text("Browse Payload"),
              ),
            ],
          ),
          const SizedBox(height: 12),
          
          TextField(
            controller: _passwordController,
            obscureText: _obscureSenderPassword,
            decoration: InputDecoration(
              labelText: "Encryption Password",
              suffixIcon: IconButton(
                icon: Icon(_obscureSenderPassword ? Icons.visibility : Icons.visibility_off),
                onPressed: () => setState(() => _obscureSenderPassword = !_obscureSenderPassword),
              ),
            ),
          ),
          const SizedBox(height: 20),

          const Text("Carrier Media Selection", style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),

          Row(
            children: [
              Expanded(child: TextField(controller: _imgCoverController, decoration: const InputDecoration(labelText: "Cover Image (.png)"))),
              const SizedBox(width: 12),
              ElevatedButton.icon(
                onPressed: () => _pickFile(_imgCoverController, ['png']),
                icon: const Icon(Icons.image),
                label: const Text("Browse Image"),
              ),
            ],
          ),
          const SizedBox(height: 8),

          Row(
            children: [
              Expanded(child: TextField(controller: _audCoverController, decoration: const InputDecoration(labelText: "Cover Audio (.wav)"))),
              const SizedBox(width: 12),
              ElevatedButton.icon(
                onPressed: () => _pickFile(_audCoverController, ['wav']),
                icon: const Icon(Icons.audio_file),
                label: const Text("Browse Audio"),
              ),
            ],
          ),
          const SizedBox(height: 8),

          Row(
            children: [
              Expanded(child: TextField(controller: _txtCoverController, decoration: const InputDecoration(labelText: "Cover Text (.tex, .txt)"))),
              const SizedBox(width: 12),
              ElevatedButton.icon(
                onPressed: () => _pickFile(_txtCoverController, ['tex', 'txt']),
                icon: const Icon(Icons.text_snippet),
                label: const Text("Browse Text"),
              ),
            ],
          ),
          
          const SizedBox(height: 12),
          CheckboxListTile(
            value: _shareOnLan,
            onChanged: _isLoading ? null : (v) => setState(() => _shareOnLan = v ?? false),
            title: const Text("Share over the local network (other devices can download the carriers)"),
            subtitle: const Text("Leave unticked to test on this computer only. The password is still required to open the file."),
            controlAffinity: ListTileControlAffinity.leading,
            contentPadding: EdgeInsets.zero,
          ),
          const SizedBox(height: 24),
          ElevatedButton.icon(
            onPressed: _isLoading ? null : _runSenderPipeline,
            icon: const Icon(Icons.security),
            label: const Text("Process Payload & Start Multi-Port Nodes"),
            style: ElevatedButton.styleFrom(minimumSize: const Size(300, 50), backgroundColor: Colors.deepPurple),
          ),
          
          if (_isLoading) const Padding(padding: EdgeInsets.only(top: 16), child: CircularProgressIndicator()),
          
          if (_statusMessage.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 16),
              child: Text(_statusMessage, style: const TextStyle(color: Colors.greenAccent, fontWeight: FontWeight.bold)),
            ),

          if (_magnetOutputController.text.isNotEmpty) ...[
            const SizedBox(height: 24),
            const Text("Generated Multi-Node Stego Magnet URI (Click & Ctrl+A to select all):", style: TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            // Replaced SelectableText with a ReadOnly TextField so Ctrl+A selects 100% cleanly without smart quotes clipping
            TextField(
              controller: _magnetOutputController,
              readOnly: true,
              maxLines: 3,
              style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
              decoration: const InputDecoration(
                border: OutlineInputBorder(),
                filled: true,
                fillColor: Colors.black45,
              ),
            ),
            const SizedBox(height: 8),
            ElevatedButton.icon(
              onPressed: () {
                Clipboard.setData(ClipboardData(text: _magnetOutputController.text));
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Complete Magnet URI copied to clipboard!")));
              },
              icon: const Icon(Icons.copy),
              label: const Text("Copy Magnet Link"),
            ),
            const SizedBox(height: 8),
            ElevatedButton.icon(
              onPressed: _isLoading ? null : _stopSeeding,
              icon: const Icon(Icons.stop_circle_outlined),
              label: const Text("Stop Seeding & Withdraw Link"),
              style: ElevatedButton.styleFrom(backgroundColor: Colors.redAccent),
            )
          ]
        ],
      ),
    );
  }

  Widget _buildReceiverPanel() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text("Phase 3: Multi-Node P2P Swarm Receiver",
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: Colors.tealAccent)),
        const SizedBox(height: 16),
        TextField(
          controller: _magnetInputController,
          maxLines: 3,
          decoration: const InputDecoration(
            labelText: "Paste Complete Stego Magnet Link (stego://dht...)",
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        
        TextField(
          controller: _receiverPasswordController,
          obscureText: _obscureReceiverPassword,
          decoration: InputDecoration(
            labelText: "Secret Decryption Password",
            suffixIcon: IconButton(
              icon: Icon(_obscureReceiverPassword ? Icons.visibility : Icons.visibility_off),
              onPressed: () => setState(() => _obscureReceiverPassword = !_obscureReceiverPassword),
            ),
          ),
        ),
        const SizedBox(height: 24),
        
        ElevatedButton.icon(
          onPressed: _isLoading ? null : _runReceiverPipeline,
          icon: const Icon(Icons.download_rounded),
          label: const Text("Start Parallel Swarm & Restore File"),
          style: ElevatedButton.styleFrom(minimumSize: const Size(300, 50), backgroundColor: Colors.teal),
        ),
        if (_isLoading) const Padding(padding: EdgeInsets.only(top: 16), child: CircularProgressIndicator()),
        if (_receiverStatusMessage.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 16),
            child: Text(_receiverStatusMessage, style: const TextStyle(color: Colors.tealAccent, fontWeight: FontWeight.bold)),
          ),
      ],
    );
  }
}