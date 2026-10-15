import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:http/http.dart' as http;
import 'package:image_picker/image_picker.dart';
import 'package:latlong2/latlong.dart';
import 'package:url_launcher/url_launcher.dart';

void main() {
  runApp(const SiteLogApp());
}

class SiteColors {
  static const paper = Color(0xFFF9FAFB);
  static const ink = Color(0xFF030712);
  static const stone = Color(0xFF6B7280);
  static const line = Color(0xFFE5E7EB);
  static const indigo = Color(0xFF9333EA);
  static const amber = Color(0xFFD97706);
  static const rose = Color(0xFFE11D48);
  static const green = Color(0xFF059669);
  static const chat = Color(0xFFF9FAFB);
  static const white = Color(0xFFFFFFFF);
  static const washRose = Color(0xFFFFF1F2);

  static Color status(String? value) {
    switch (value) {
      case 'in_progress':
        return amber;
      case 'discarded':
        return rose;
      case 'completed':
        return green;
      default:
        return const Color(0xFF7E22CE);
    }
  }
}

const sections = [
  ('new', 'New jobs', 'Waiting to start'),
  ('in_progress', 'In progress jobs', 'In progress'),
  ('discarded', 'Discarded jobs', 'Discarded'),
  ('completed', 'Completed jobs', 'Completed'),
];

class SiteLogApp extends StatelessWidget {
  const SiteLogApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'SiteLog',
      theme: ThemeData(
        useMaterial3: true,
        scaffoldBackgroundColor: SiteColors.paper,
        colorScheme: ColorScheme.fromSeed(seedColor: SiteColors.indigo, primary: SiteColors.ink, surface: SiteColors.white),
        appBarTheme: const AppBarTheme(
          backgroundColor: SiteColors.ink,
          foregroundColor: SiteColors.white,
          elevation: 0,
          centerTitle: false,
          titleTextStyle: TextStyle(color: SiteColors.white, fontSize: 22, fontWeight: FontWeight.w900),
        ),
        filledButtonTheme: FilledButtonThemeData(
          style: FilledButton.styleFrom(
            backgroundColor: SiteColors.ink,
            foregroundColor: Colors.white,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          ),
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: Colors.white,
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: SiteColors.line)),
          enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: SiteColors.line)),
          focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: SiteColors.indigo, width: 1.6)),
        ),
      ),
      home: const LoginScreen(),
    );
  }
}

class Api {
  Api(this.baseUrl, this.token, this.user);

  final String baseUrl;
  final String token;
  final Map<String, dynamic> user;

  bool get isAdmin => user['role'] == 'office';

  Map<String, String> get _headers => {
        'Authorization': 'Bearer $token',
        'Content-Type': 'application/json',
      };

  Future<Map<String, dynamic>> jobs({String? status}) async {
    final query = status == null ? '' : '?status=$status';
    final response = await http.get(Uri.parse('$baseUrl/api/jobs$query'), headers: _headers);
    _check(response);
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> job(String id) async {
    final response = await http.get(Uri.parse('$baseUrl/api/jobs/$id'), headers: _headers);
    _check(response);
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<List<dynamic>> engineers() async {
    final response = await http.get(Uri.parse('$baseUrl/api/engineers'), headers: _headers);
    _check(response);
    return jsonDecode(response.body)['engineers'] as List<dynamic>;
  }

  Future<Map<String, dynamic>> createJob(Map<String, dynamic> body) async {
    final response = await http.post(Uri.parse('$baseUrl/api/jobs'), headers: _headers, body: jsonEncode(body));
    _check(response);
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<void> discard(String id, String reason) async {
    final response = await http.post(
      Uri.parse('$baseUrl/api/jobs/$id/discard'),
      headers: _headers,
      body: jsonEncode({'reason': reason}),
    );
    _check(response);
  }

  Future<void> restore(String id) async {
    final response = await http.post(Uri.parse('$baseUrl/api/jobs/$id/restore'), headers: _headers);
    _check(response);
  }

  static String normalizeBaseUrl(String raw) {
    var url = raw.trim();
    if (url.isEmpty) {
      return 'http://187.127.158.102:8010';
    }
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      url = 'http://$url';
    }
    while (url.endsWith('/')) {
      url = url.substring(0, url.length - 1);
    }
    final uri = Uri.tryParse(url);
    if (uri != null && (uri.host == '187.127.158.102' || uri.host == '10.0.2.2') && !uri.hasPort) {
      url = '${uri.scheme}://${uri.host}:8010';
    }
    return url;
  }

  Future<List<dynamic>> chats() async {
    try {
      final response = await http.get(Uri.parse('$baseUrl/api/chats'), headers: _headers);
      if (response.statusCode == 200) {
        final decoded = jsonDecode(response.body);
        if (decoded is Map<String, dynamic> && decoded['chats'] is List) {
          return decoded['chats'] as List<dynamic>;
        }
      }
    } catch (_) {
      // Fall through to jobs fallback
    }

    // Fallback: If /api/chats is 404 or fails on older server versions,
    // load jobs and adapt them as chat threads
    final response = await http.get(Uri.parse('$baseUrl/api/jobs'), headers: _headers);
    _check(response);
    final data = jsonDecode(response.body) as Map<String, dynamic>;
    final list = (data['jobs'] as List<dynamic>?) ?? [];
    return list.map((item) {
      final job = Map<String, dynamic>.from(item as Map);
      job['last_body'] ??= job['issue'] ?? '';
      job['when'] ??= job['job_date'] ?? '';
      return job;
    }).toList();
  }

  Future<Map<String, dynamic>> counts() async {
    final response = await http.get(Uri.parse('$baseUrl/api/jobs'), headers: _headers);
    _check(response);
    final data = jsonDecode(response.body) as Map<String, dynamic>;
    return (data['counts'] as Map<String, dynamic>?) ?? {};
  }

  Future<Map<String, dynamic>> routePlan({
    String startAddress = 'HQ Office, 14 Baker Street, London NW1 6XE',
    bool roundTrip = false,
    String date = '',
    String engineerId = '',
    String status = '',
  }) async {
    final query = '?start_address=${Uri.encodeComponent(startAddress)}&round_trip=$roundTrip&date=${Uri.encodeComponent(date)}&engineer_id=${Uri.encodeComponent(engineerId)}&status=${Uri.encodeComponent(status)}';
    final response = await http.get(Uri.parse('$baseUrl/api/route-plan$query'), headers: _headers);
    _check(response);
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<List<dynamic>> messages(String id, {int limit = 50}) async {
    final response = await http.get(Uri.parse('$baseUrl/api/jobs/$id/messages?limit=$limit'), headers: _headers);
    _check(response);
    return jsonDecode(response.body)['messages'] as List<dynamic>;
  }

  Future<void> sendMessage(String id, String body) async {
    final response = await http.post(
      Uri.parse('$baseUrl/api/jobs/$id/messages'),
      headers: _headers,
      body: jsonEncode({'body': body}),
    );
    _check(response);
  }

  Future<Map<String, dynamic>> mailbox() async {
    final response = await http.get(Uri.parse('$baseUrl/api/mailbox'), headers: _headers);
    _check(response);
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<void> saveMailbox(String host, String email, String password, int port) async {
    final response = await http.put(
      Uri.parse('$baseUrl/api/mailbox'),
      headers: _headers,
      body: jsonEncode({'host': host, 'email': email, 'password': password, 'port': port}),
    );
    _check(response);
  }

  Future<void> uploadPhoto(String jobId, File file) async {
    final request = http.MultipartRequest('POST', Uri.parse('$baseUrl/api/jobs/$jobId/photos'));
    request.headers['Authorization'] = 'Bearer $token';
    request.files.add(await http.MultipartFile.fromPath('file', file.path));
    final response = await http.Response.fromStream(await request.send());
    _check(response);
  }

  static Future<Api> login(String baseUrl, String email, String password) async {
    final cleanBase = normalizeBaseUrl(baseUrl);
    final response = await http.post(
      Uri.parse('$cleanBase/api/login'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'email': email, 'password': password}),
    );
    _check(response);
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    return Api(cleanBase, body['token'] as String, body['user'] as Map<String, dynamic>);
  }

  static void _check(http.Response response) {
    if (response.statusCode >= 400) {
      throw Exception('Request failed (${response.statusCode})');
    }
  }
}

class StatusPill extends StatelessWidget {
  const StatusPill({super.key, required this.status, required this.label});

  final String? status;
  final String? label;

  @override
  Widget build(BuildContext context) {
    final color = SiteColors.status(status);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(999)),
      child: Text(
        (label ?? status ?? '').toUpperCase(),
        style: TextStyle(color: color, fontSize: 10, fontWeight: FontWeight.w800, letterSpacing: 0.6),
      ),
    );
  }
}

class JobCard extends StatelessWidget {
  const JobCard({super.key, required this.job, required this.onTap});

  final Map<String, dynamic> job;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = SiteColors.status(job['status']?.toString());
    final issue = (job['issue'] ?? '').toString();
    final meta = [
      job['client'],
      job['engineers'],
      job['job_date'],
      (job['source'] ?? '').toString().toUpperCase(),
    ].where((part) => part != null && part.toString().isNotEmpty).map((part) => part.toString()).join('  ·  ');
    return Material(
      color: Colors.white,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20), side: const BorderSide(color: SiteColors.line)),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Container(width: 6, color: color),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(14, 14, 14, 14),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(
                            child: Text(job['work_ref']?.toString() ?? '', style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w900, height: 1.05, color: SiteColors.ink)),
                          ),
                          const SizedBox(width: 8),
                          StatusPill(status: job['status']?.toString(), label: job['status_label']?.toString()),
                        ],
                      ),
                      const SizedBox(height: 6),
                      Text(
                        job['site_address']?.toString() ?? '',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: Color(0xFF57534E), fontSize: 13),
                      ),
                      if (issue.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        Text(issue, maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 14, height: 1.3)),
                      ],
                      if (meta.isNotEmpty) ...[
                        const SizedBox(height: 10),
                        Text(meta, style: const TextStyle(fontSize: 12, color: SiteColors.stone)),
                      ],
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _base = TextEditingController(text: '187.127.158.102');
  final _email = TextEditingController(text: 'alex@sitelog.demo');
  final _password = TextEditingController(text: 'engineer123');
  String _error = '';
  bool _busy = false;

  @override
  void dispose() {
    _base.dispose();
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      final api = await Api.login(_base.text, _email.text.trim(), _password.text);
      if (!mounted) return;
      await Navigator.of(context).push(MaterialPageRoute(builder: (_) => HomeScreen(api: api)));
    } catch (e) {
      setState(() => _error = 'Could not sign in: ${e.toString().replaceAll("Exception: ", "")}');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('SiteLog')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Container(
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(24),
              border: Border.all(color: SiteColors.line),
            ),
            clipBehavior: Clip.antiAlias,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Container(
                  color: SiteColors.ink,
                  padding: const EdgeInsets.fromLTRB(20, 20, 20, 18),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('JOB LOG', style: TextStyle(color: Color(0xFFA8A29E), fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 1.4)),
                      const SizedBox(height: 4),
                      Text('Sign in', style: const TextStyle(color: SiteColors.white, fontSize: 28, fontWeight: FontWeight.w900)),
                      const SizedBox(height: 6),
                      const Text('Office dashboard and engineer reports.', style: TextStyle(color: Color(0xFFD6D3D1), fontSize: 13)),
                    ],
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.all(20),
                  child: Column(
                    children: [
                      TextField(controller: _base, decoration: const InputDecoration(labelText: 'Server')),
                      const SizedBox(height: 12),
                      TextField(controller: _email, decoration: const InputDecoration(labelText: 'Email')),
                      const SizedBox(height: 12),
                      TextField(controller: _password, obscureText: true, decoration: const InputDecoration(labelText: 'Password')),
                      if (_error.isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 12),
                          child: Text(_error, style: const TextStyle(color: SiteColors.rose, fontWeight: FontWeight.w600)),
                        ),
                      const SizedBox(height: 16),
                      SizedBox(width: double.infinity, child: FilledButton(onPressed: _busy ? null : _submit, child: Text(_busy ? 'Signing in...' : 'Sign in'))),
                      const SizedBox(height: 16),
                      Container(
                        width: double.infinity,
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(color: SiteColors.paper, borderRadius: BorderRadius.circular(16)),
                        child: const Text(
                          'Office  office@sitelog.demo / office123\nEngineers  alex@sitelog.demo or sam@sitelog.demo / engineer123',
                          style: TextStyle(fontSize: 12, color: SiteColors.stone, height: 1.5),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class AppDrawer extends StatelessWidget {
  const AppDrawer({super.key, required this.api, required this.currentRoute});

  final Api api;
  final String currentRoute;

  @override
  Widget build(BuildContext context) {
    final role = api.isAdmin ? 'Office' : 'Engineer';
    return Drawer(
      backgroundColor: SiteColors.paper,
      child: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(
              padding: const EdgeInsets.fromLTRB(18, 18, 18, 16),
              color: SiteColors.ink,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Container(
                        width: 44,
                        height: 44,
                        decoration: BoxDecoration(
                          color: SiteColors.indigo.withValues(alpha: 0.25),
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(color: SiteColors.indigo.withValues(alpha: 0.5)),
                        ),
                        alignment: Alignment.center,
                        child: Text(
                          (api.user['name']?.toString() ?? 'U').substring(0, 1).toUpperCase(),
                          style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w900),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              api.user['name']?.toString() ?? 'User',
                              style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w900),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                            const SizedBox(height: 2),
                            Text(
                              api.user['email']?.toString() ?? '',
                              style: const TextStyle(color: Color(0xFFA8A29E), fontSize: 11),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: Colors.white.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: Text(
                      role.toUpperCase(),
                      style: const TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.w800, letterSpacing: 0.8),
                    ),
                  ),
                ],
              ),
            ),
            Expanded(
              child: FutureBuilder<Map<String, dynamic>>(
                future: api.counts(),
                builder: (context, snapshot) {
                  final counts = snapshot.data ?? {};
                  return ListView(
                    padding: const EdgeInsets.symmetric(vertical: 8),
                    children: [
                      _DrawerTile(
                        icon: Icons.chat_bubble_outline,
                        title: 'Chats',
                        selected: currentRoute == 'chats',
                        onTap: () {
                          Navigator.of(context).pop();
                          if (currentRoute != 'chats') {
                            Navigator.of(context).pushReplacement(
                              MaterialPageRoute(builder: (_) => HomeScreen(api: api)),
                            );
                          }
                        },
                      ),
                      _DrawerTile(
                        icon: Icons.dashboard_outlined,
                        title: 'Job Board',
                        selected: currentRoute == 'dashboard',
                        onTap: () {
                          Navigator.of(context).pop();
                          if (currentRoute != 'dashboard') {
                            Navigator.of(context).push(
                              MaterialPageRoute(builder: (_) => DashboardScreen(api: api)),
                            );
                          }
                        },
                      ),
                      _DrawerTile(
                        icon: Icons.route_outlined,
                        title: 'Route Planner',
                        selected: currentRoute == 'route',
                        onTap: () {
                          Navigator.of(context).pop();
                          if (currentRoute != 'route') {
                            Navigator.of(context).push(
                              MaterialPageRoute(builder: (_) => RoutePlannerScreen(api: api)),
                            );
                          }
                        },
                      ),
                      const Padding(
                        padding: EdgeInsets.fromLTRB(16, 14, 16, 6),
                        child: Text(
                          'CATEGORIES',
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w800,
                            letterSpacing: 1.2,
                            color: SiteColors.stone,
                          ),
                        ),
                      ),
                      for (final section in sections)
                        _DrawerCategoryTile(
                          status: section.$1,
                          title: section.$2,
                          count: counts[section.$1] ?? 0,
                          selected: currentRoute == section.$1,
                          onTap: () {
                            Navigator.of(context).pop();
                            Navigator.of(context).push(
                              MaterialPageRoute(
                                builder: (_) => SectionScreen(api: api, status: section.$1, title: section.$2),
                              ),
                            );
                          },
                        ),
                      if (api.isAdmin) ...[
                        const Padding(
                          padding: EdgeInsets.fromLTRB(16, 14, 16, 6),
                          child: Text(
                            'OFFICE',
                            style: TextStyle(
                              fontSize: 11,
                              fontWeight: FontWeight.w800,
                              letterSpacing: 1.2,
                              color: SiteColors.stone,
                            ),
                          ),
                        ),
                        _DrawerTile(
                          icon: Icons.mail_outline,
                          title: 'Mailbox',
                          selected: currentRoute == 'mailbox',
                          onTap: () {
                            Navigator.of(context).pop();
                            Navigator.of(context).push(
                              MaterialPageRoute(builder: (_) => MailboxScreen(api: api)),
                            );
                          },
                        ),
                      ],
                    ],
                  );
                },
              ),
            ),
            const Divider(height: 1, color: SiteColors.line),
            ListTile(
              leading: const Icon(Icons.logout, color: SiteColors.rose),
              title: const Text('Sign out', style: TextStyle(color: SiteColors.rose, fontWeight: FontWeight.w700)),
              onTap: () {
                Navigator.of(context).pushAndRemoveUntil(
                  MaterialPageRoute(builder: (_) => const LoginScreen()),
                  (route) => false,
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}

class _DrawerTile extends StatelessWidget {
  const _DrawerTile({
    required this.icon,
    required this.title,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 10, vertical: 2),
      decoration: BoxDecoration(
        color: selected ? SiteColors.indigo.withValues(alpha: 0.1) : Colors.transparent,
        borderRadius: BorderRadius.circular(12),
      ),
      child: ListTile(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        dense: true,
        leading: Icon(icon, color: selected ? SiteColors.indigo : SiteColors.ink, size: 20),
        title: Text(
          title,
          style: TextStyle(
            color: selected ? SiteColors.indigo : SiteColors.ink,
            fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
            fontSize: 14,
          ),
        ),
        onTap: onTap,
      ),
    );
  }
}

class _DrawerCategoryTile extends StatelessWidget {
  const _DrawerCategoryTile({
    required this.status,
    required this.title,
    required this.count,
    required this.selected,
    required this.onTap,
  });

  final String status;
  final String title;
  final Object count;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = SiteColors.status(status);
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 10, vertical: 2),
      decoration: BoxDecoration(
        color: selected ? color.withValues(alpha: 0.1) : Colors.transparent,
        borderRadius: BorderRadius.circular(12),
      ),
      child: ListTile(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        dense: true,
        leading: Container(
          width: 10,
          height: 10,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        title: Text(
          title,
          style: TextStyle(
            color: selected ? color : SiteColors.ink,
            fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
            fontSize: 14,
          ),
        ),
        trailing: Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
          decoration: BoxDecoration(
            color: color.withValues(alpha: 0.14),
            borderRadius: BorderRadius.circular(999),
          ),
          child: Text(
            '$count',
            style: TextStyle(
              color: color,
              fontSize: 11,
              fontWeight: FontWeight.w800,
            ),
          ),
        ),
        onTap: onTap,
      ),
    );
  }
}

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.api});

  final Api api;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  late Future<List<dynamic>> _chats;
  Timer? _pollTimer;

  @override
  void initState() {
    super.initState();
    _chats = widget.api.chats();
    _pollTimer = Timer.periodic(const Duration(seconds: 8), (_) {
      if (mounted) _reload();
    });
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  Future<void> _reload() async {
    setState(() => _chats = widget.api.chats());
    try {
      await _chats;
    } catch (_) {}
  }

  Future<void> _openChat(Map<String, dynamic> job) async {
    await Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => ChatScreen(api: widget.api, job: job)),
    );
    await _reload();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Chats'),
        actions: [
          IconButton(
            tooltip: 'Job Board',
            icon: const Icon(Icons.dashboard_outlined),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => DashboardScreen(api: widget.api)),
            ),
          ),
        ],
      ),
      drawer: AppDrawer(api: widget.api, currentRoute: 'chats'),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: SiteColors.ink,
        foregroundColor: Colors.white,
        onPressed: () async {
          final saved = await Navigator.of(context).push<bool>(
            MaterialPageRoute(builder: (_) => NewJobScreen(api: widget.api)),
          );
          if (saved == true) await _reload();
        },
        icon: const Icon(Icons.add),
        label: const Text('New job'),
      ),
      body: RefreshIndicator(
        onRefresh: _reload,
        child: FutureBuilder<List<dynamic>>(
          future: _chats,
          builder: (context, snapshot) {
            if (snapshot.connectionState != ConnectionState.done && !snapshot.hasData) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snapshot.hasError) {
              return ListView(
                padding: const EdgeInsets.all(24),
                children: [
                  Center(
                    child: Column(
                      children: [
                        const Icon(Icons.error_outline, size: 48, color: SiteColors.stone),
                        const SizedBox(height: 12),
                        Text(
                          snapshot.error != null
                              ? 'Could not load conversations: ${snapshot.error.toString().replaceAll("Exception: ", "")}'
                              : 'Could not load conversations.',
                          textAlign: TextAlign.center,
                          style: const TextStyle(color: SiteColors.stone),
                        ),
                        const SizedBox(height: 12),
                        FilledButton(onPressed: _reload, child: const Text('Retry')),
                      ],
                    ),
                  ),
                ],
              );
            }
            final chats = snapshot.data ?? [];
            if (chats.isEmpty) {
              return ListView(
                padding: const EdgeInsets.all(32),
                children: [
                  const SizedBox(height: 48),
                  Container(
                    width: 72,
                    height: 72,
                    decoration: BoxDecoration(
                      color: SiteColors.indigo.withValues(alpha: 0.1),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.forum_outlined, size: 36, color: SiteColors.indigo),
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    'No conversations yet',
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: SiteColors.ink),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Conversations will appear here for each job on your board. Create a job to get started.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: SiteColors.stone, fontSize: 13, height: 1.4),
                  ),
                ],
              );
            }

            return ListView.separated(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 96),
              itemCount: chats.length,
              separatorBuilder: (_, __) => const SizedBox(height: 10),
              itemBuilder: (context, index) {
                final chat = chats[index] as Map<String, dynamic>;
                return _ChatThreadTile(
                  chat: chat,
                  onTap: () => _openChat(chat),
                );
              },
            );
          },
        ),
      ),
    );
  }
}

class _ChatThreadTile extends StatelessWidget {
  const _ChatThreadTile({required this.chat, required this.onTap});

  final Map<String, dynamic> chat;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final workRef = chat['work_ref']?.toString() ?? 'Job';
    final address = chat['site_address']?.toString() ?? '';
    final lastBody = (chat['last_body'] ?? '').toString().trim();
    final when = (chat['when'] ?? '').toString();
    final timeStr = when.isNotEmpty ? when.split(', ').first : _shortWhen(chat['last_at']?.toString());
    final status = chat['status']?.toString();
    final statusLabel = chat['status_label']?.toString();
    final initials = workRef.length >= 2
        ? workRef.substring(0, 2).toUpperCase()
        : (workRef.isNotEmpty ? workRef.toUpperCase() : 'JB');

    return Material(
      color: Colors.white,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: const BorderSide(color: SiteColors.line),
      ),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                  color: const Color(0xFFFAF5FF),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: const Color(0xFFE9D5FF)),
                ),
                alignment: Alignment.center,
                child: Text(
                  initials,
                  style: const TextStyle(
                    color: Color(0xFF9333EA),
                    fontSize: 14,
                    fontWeight: FontWeight.w900,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Flexible(
                          child: Text(
                            workRef,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.w800,
                              color: SiteColors.ink,
                            ),
                          ),
                        ),
                        const SizedBox(width: 6),
                        StatusPill(status: status, label: statusLabel),
                        const Spacer(),
                        if (timeStr.isNotEmpty)
                          Text(
                            timeStr,
                            style: const TextStyle(fontSize: 11, color: SiteColors.stone),
                          ),
                      ],
                    ),
                    if (address.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text(
                        address,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 12, color: SiteColors.stone),
                      ),
                    ],
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        const Icon(Icons.chat_bubble_outline, size: 13, color: Color(0xFF9CA3AF)),
                        const SizedBox(width: 5),
                        Expanded(
                          child: Text(
                            lastBody.isNotEmpty ? lastBody : 'No messages yet · Tap to start chat',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontSize: 13,
                              color: lastBody.isNotEmpty ? const Color(0xFF374151) : const Color(0xFF9CA3AF),
                              fontStyle: lastBody.isNotEmpty ? FontStyle.normal : FontStyle.italic,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key, required this.api});

  final Api api;

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  late Future<Map<String, dynamic>> _data;

  @override
  void initState() {
    super.initState();
    _data = widget.api.jobs();
  }

  Future<void> _reload() async {
    setState(() => _data = widget.api.jobs());
    await _data;
  }

  Future<void> _openJob(String id) async {
    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => JobDetailScreen(api: widget.api, jobId: id)));
    await _reload();
  }

  @override
  Widget build(BuildContext context) {
    final role = widget.api.isAdmin ? 'Office' : 'Engineer';
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.api.user['name']?.toString() ?? 'Job Board'),
        actions: [
          IconButton(
            tooltip: 'Chats',
            onPressed: () => Navigator.of(context).pushReplacement(
              MaterialPageRoute(builder: (_) => HomeScreen(api: widget.api)),
            ),
            icon: const Icon(Icons.chat_bubble_outline),
          ),
          if (widget.api.isAdmin)
            IconButton(
              tooltip: 'Mailbox',
              onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => MailboxScreen(api: widget.api))),
              icon: const Icon(Icons.mail_outline),
            ),
        ],
      ),
      drawer: AppDrawer(api: widget.api, currentRoute: 'dashboard'),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: SiteColors.ink,
        foregroundColor: Colors.white,
        onPressed: () async {
          final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => NewJobScreen(api: widget.api)));
          if (saved == true) await _reload();
        },
        icon: const Icon(Icons.add),
        label: const Text('New job'),
      ),
      body: RefreshIndicator(
        onRefresh: _reload,
        child: FutureBuilder<Map<String, dynamic>>(
          future: _data,
          builder: (context, snapshot) {
            if (snapshot.connectionState != ConnectionState.done && !snapshot.hasData) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snapshot.hasError) {
              return ListView(children: const [Padding(padding: EdgeInsets.all(24), child: Text('Could not load jobs.'))]);
            }
            final counts = snapshot.data!['counts'] as Map<String, dynamic>;
            final jobs = (snapshot.data!['jobs'] as List<dynamic>).take(8).toList();
            return ListView(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
              children: [
                Text(role.toUpperCase(), style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 1.2, color: SiteColors.stone)),
                const SizedBox(height: 4),
                Text(widget.api.isAdmin ? 'Every job on the board.' : 'Jobs assigned to you.', style: const TextStyle(color: SiteColors.stone)),
                const SizedBox(height: 16),
                GridView.count(
                  crossAxisCount: 2,
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  mainAxisSpacing: 12,
                  crossAxisSpacing: 12,
                  childAspectRatio: 1.35,
                  children: [
                    for (final section in sections)
                      _SectionTile(
                        status: section.$1,
                        title: section.$2,
                        blurb: section.$3,
                        count: counts[section.$1] ?? 0,
                        onTap: () => Navigator.of(context).push(
                          MaterialPageRoute(builder: (_) => SectionScreen(api: widget.api, status: section.$1, title: section.$2)),
                        ),
                      ),
                  ],
                ),
                const SizedBox(height: 22),
                Text('Latest jobs', style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w900, color: SiteColors.ink)),
                const SizedBox(height: 12),
                for (final raw in jobs) ...[
                  JobCard(job: raw as Map<String, dynamic>, onTap: () => _openJob(raw['id'] as String)),
                  const SizedBox(height: 12),
                ],
                if (jobs.isEmpty) const Text('No jobs yet.', style: TextStyle(color: SiteColors.stone)),
              ],
            );
          },
        ),
      ),
    );
  }
}

class RoutePlannerScreen extends StatefulWidget {
  const RoutePlannerScreen({super.key, required this.api});

  final Api api;

  @override
  State<RoutePlannerScreen> createState() => _RoutePlannerScreenState();
}

class _RoutePlannerScreenState extends State<RoutePlannerScreen> {
  final _startController = TextEditingController(text: 'HQ Office, 14 Baker Street, London NW1 6XE');
  bool _roundTrip = false;
  late Future<Map<String, dynamic>> _plan;

  @override
  void initState() {
    super.initState();
    _fetchPlan();
  }

  @override
  void dispose() {
    _startController.dispose();
    super.dispose();
  }

  void _fetchPlan() {
    setState(() {
      _plan = widget.api.routePlan(
        startAddress: _startController.text.trim(),
        roundTrip: _roundTrip,
      );
    });
  }

  Future<void> _launchUrl(String url) async {
    final uri = Uri.parse(url);
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    } else {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Could not open map URL.')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Route Planner'),
        actions: [
          IconButton(
            tooltip: 'Recalculate',
            icon: const Icon(Icons.refresh),
            onPressed: _fetchPlan,
          ),
        ],
      ),
      drawer: AppDrawer(api: widget.api, currentRoute: 'route'),
      body: FutureBuilder<Map<String, dynamic>>(
        future: _plan,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done && !snapshot.hasData) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snapshot.hasError) {
            return ListView(
              padding: const EdgeInsets.all(24),
              children: [
                Center(
                  child: Column(
                    children: [
                      const Icon(Icons.error_outline, size: 48, color: SiteColors.stone),
                      const SizedBox(height: 12),
                      const Text('Could not calculate route.', style: TextStyle(color: SiteColors.stone)),
                      const SizedBox(height: 12),
                      FilledButton(onPressed: _fetchPlan, child: const Text('Retry')),
                    ],
                  ),
                ),
              ],
            );
          }

          final plan = snapshot.data ?? {};
          final stops = (plan['stops'] as List<dynamic>?) ?? [];
          final totalMiles = plan['total_distance_miles'] ?? 0.0;
          final totalMins = plan['total_duration_mins'] ?? 0;
          final gmapsUrl = (plan['google_maps_url'] ?? '').toString();

          return ListView(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
            children: [
              // Start Location Card
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(color: SiteColors.line),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'START LOCATION',
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w800,
                        letterSpacing: 1,
                        color: SiteColors.stone,
                      ),
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      controller: _startController,
                      decoration: const InputDecoration(
                        hintText: 'Enter start depot or address',
                        prefixIcon: Icon(Icons.flag, color: SiteColors.green, size: 20),
                      ),
                      onSubmitted: (_) => _fetchPlan(),
                    ),
                    const SizedBox(height: 10),
                    Row(
                      children: [
                        Checkbox(
                          value: _roundTrip,
                          activeColor: SiteColors.indigo,
                          onChanged: (val) {
                            setState(() => _roundTrip = val ?? false);
                            _fetchPlan();
                          },
                        ),
                        const Text('Return to Start (Round Trip)', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                        const Spacer(),
                        FilledButton(
                          onPressed: _fetchPlan,
                          child: const Text('Recalculate'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),

              const SizedBox(height: 14),

              // Summary Stats Card
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  gradient: const LinearGradient(
                    colors: [Color(0xFF9333EA), Color(0xFF7E22CE)],
                  ),
                  borderRadius: BorderRadius.circular(20),
                  boxShadow: [
                    BoxShadow(
                      color: SiteColors.indigo.withValues(alpha: 0.3),
                      blurRadius: 12,
                      offset: const Offset(0, 4),
                    ),
                  ],
                ),
                child: Column(
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceAround,
                      children: [
                        Column(
                          children: [
                            const Text('TOTAL DISTANCE', style: TextStyle(color: Color(0xFFE9D5FF), fontSize: 10, fontWeight: FontWeight.w800, letterSpacing: 0.8)),
                            const SizedBox(height: 4),
                            Text('$totalMiles mi', style: const TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.w900)),
                          ],
                        ),
                        Container(width: 1, height: 32, color: Colors.white.withValues(alpha: 0.2)),
                        Column(
                          children: [
                            const Text('EST. DRIVE TIME', style: TextStyle(color: Color(0xFFE9D5FF), fontSize: 10, fontWeight: FontWeight.w800, letterSpacing: 0.8)),
                            const SizedBox(height: 4),
                            Text('$totalMins mins', style: const TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.w900)),
                          ],
                        ),
                        Container(width: 1, height: 32, color: Colors.white.withValues(alpha: 0.2)),
                        Column(
                          children: [
                            const Text('TOTAL STOPS', style: TextStyle(color: Color(0xFFE9D5FF), fontSize: 10, fontWeight: FontWeight.w800, letterSpacing: 0.8)),
                            const SizedBox(height: 4),
                            Text('${stops.length}', style: const TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.w900)),
                          ],
                        ),
                      ],
                    ),
                    if (gmapsUrl.isNotEmpty) ...[
                      const SizedBox(height: 14),
                      SizedBox(
                        width: double.infinity,
                        child: ElevatedButton.icon(
                          style: ElevatedButton.styleFrom(
                            backgroundColor: Colors.white,
                            foregroundColor: SiteColors.ink,
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                            padding: const EdgeInsets.symmetric(vertical: 12),
                          ),
                          onPressed: () => _launchUrl(gmapsUrl),
                          icon: const Icon(Icons.navigation_outlined, color: SiteColors.indigo, size: 20),
                          label: const Text('Open in Google Maps', style: TextStyle(fontWeight: FontWeight.w800)),
                        ),
                      ),
                    ],
                  ],
                ),
              ),

              const SizedBox(height: 16),

              // Interactive Route Map
              _RouteMapCard(
                plan: plan,
                api: widget.api,
                onNavigate: _launchUrl,
              ),

              const SizedBox(height: 20),

              const Text('OPTIMIZED STOP SEQUENCE', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 1, color: SiteColors.stone)),
              const SizedBox(height: 10),

              // Start Depot Card
              Container(
                margin: const EdgeInsets.only(bottom: 10),
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                decoration: BoxDecoration(
                  color: const Color(0xFFECFDF5),
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: const Color(0xFFA7F3D0)),
                ),
                child: Row(
                  children: [
                    Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        color: SiteColors.green,
                        borderRadius: BorderRadius.circular(10),
                      ),
                      alignment: Alignment.center,
                      child: const Icon(Icons.play_arrow, color: Colors.white, size: 20),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('START LOCATION', style: TextStyle(color: Color(0xFF047857), fontSize: 10, fontWeight: FontWeight.w800)),
                          const SizedBox(height: 2),
                          Text(plan['start']?['address']?.toString() ?? _startController.text, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700)),
                        ],
                      ),
                    ),
                  ],
                ),
              ),

              // Stops
              if (stops.isEmpty)
                const Padding(
                  padding: EdgeInsets.all(24),
                  child: Center(child: Text('No active jobs to route today.', style: TextStyle(color: SiteColors.stone))),
                ),

              for (final raw in stops) ...[
                _RouteStopCard(
                  stop: raw as Map<String, dynamic>,
                  api: widget.api,
                  onNavigate: () {
                    final navUrl = raw['nav_url']?.toString() ?? '';
                    if (navUrl.isNotEmpty) _launchUrl(navUrl);
                  },
                ),
                const SizedBox(height: 10),
              ],

              // Return to Start Card if Round Trip
              if (_roundTrip && stops.isNotEmpty && plan['return_leg'] != null)
                Container(
                  margin: const EdgeInsets.only(top: 4),
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                  decoration: BoxDecoration(
                    color: const Color(0xFFECFDF5),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: const Color(0xFFA7F3D0)),
                  ),
                  child: Row(
                    children: [
                      Container(
                        width: 36,
                        height: 36,
                        decoration: BoxDecoration(
                          color: SiteColors.green,
                          borderRadius: BorderRadius.circular(10),
                        ),
                        alignment: Alignment.center,
                        child: const Icon(Icons.flag_circle, color: Colors.white, size: 20),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('END DESTINATION', style: TextStyle(color: Color(0xFF047857), fontSize: 10, fontWeight: FontWeight.w800)),
                            const SizedBox(height: 2),
                            Text('Return to ${plan['start']?['address'] ?? _startController.text}', style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700)),
                            const SizedBox(height: 2),
                            Text(
                              '+${plan['return_leg']['leg_distance_miles']} mi · ${plan['return_leg']['leg_duration_mins']} mins',
                              style: const TextStyle(fontSize: 11, color: Color(0xFF047857), fontWeight: FontWeight.w700),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
            ],
          );
        },
      ),
    );
  }
}

class _RouteMapCard extends StatefulWidget {
  const _RouteMapCard({
    required this.plan,
    required this.api,
    required this.onNavigate,
  });

  final Map<String, dynamic> plan;
  final Api api;
  final void Function(String url) onNavigate;

  @override
  State<_RouteMapCard> createState() => _RouteMapCardState();
}

class _RouteMapCardState extends State<_RouteMapCard> {
  final MapController _mapController = MapController();

  List<LatLng> _getRoutePoints() {
    final geoCoords = widget.plan['geometry_coords'] as List<dynamic>?;
    if (geoCoords != null && geoCoords.isNotEmpty) {
      final roadPoints = <LatLng>[];
      for (final pt in geoCoords) {
        if (pt is List && pt.length >= 2) {
          final lng = (pt[0] as num?)?.toDouble();
          final lat = (pt[1] as num?)?.toDouble();
          if (lat != null && lng != null) {
            roadPoints.add(LatLng(lat, lng));
          }
        }
      }
      if (roadPoints.isNotEmpty) return roadPoints;
    }

    final start = widget.plan['start'] as Map<String, dynamic>?;
    final startLat = (start?['lat'] as num?)?.toDouble();
    final startLng = (start?['lng'] as num?)?.toDouble();
    final stops = (widget.plan['stops'] as List<dynamic>?) ?? [];
    final roundTrip = widget.plan['round_trip'] == true;

    final points = <LatLng>[];
    if (startLat != null && startLng != null) {
      points.add(LatLng(startLat, startLng));
    }
    for (final raw in stops) {
      if (raw is Map) {
        final lat = (raw['lat'] as num?)?.toDouble();
        final lng = (raw['lng'] as num?)?.toDouble();
        if (lat != null && lng != null) {
          points.add(LatLng(lat, lng));
        }
      }
    }
    if (roundTrip && startLat != null && startLng != null && points.length > 1) {
      points.add(LatLng(startLat, startLng));
    }
    return points;
  }

  void _fitAll(List<LatLng> points) {
    if (points.length > 1) {
      _mapController.fitCamera(
        CameraFit.bounds(
          bounds: LatLngBounds.fromPoints(points),
          padding: const EdgeInsets.all(36),
        ),
      );
    } else if (points.isNotEmpty) {
      _mapController.move(points.first, 14.0);
    }
  }

  void _showStopSheet(BuildContext context, Map<String, dynamic> stop) {
    final stopNum = stop['stop_number'] ?? 1;
    final workRef = stop['work_ref']?.toString() ?? 'Job';
    final address = stop['site_address']?.toString() ?? '';
    final client = stop['client']?.toString() ?? '';
    final navUrl = stop['nav_url']?.toString() ?? '';
    final jobId = stop['job_id']?.toString() ?? '';
    final legDist = stop['leg_distance_miles'] ?? 0.0;
    final legDur = stop['leg_duration_mins'] ?? 0;

    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                    color: SiteColors.line,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  Container(
                    width: 32,
                    height: 32,
                    decoration: BoxDecoration(
                      color: const Color(0xFF9333EA),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    alignment: Alignment.center,
                    child: Text(
                      '$stopNum',
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.w900,
                        fontSize: 15,
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          workRef,
                          style: const TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.w900,
                            color: SiteColors.ink,
                          ),
                        ),
                        if (client.isNotEmpty)
                          Text(
                            client,
                            style: const TextStyle(
                              fontSize: 12,
                              color: SiteColors.stone,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                      ],
                    ),
                  ),
                  StatusPill(
                    status: stop['status']?.toString(),
                    label: stop['status_label']?.toString(),
                  ),
                ],
              ),
              if (address.isNotEmpty) ...[
                const SizedBox(height: 12),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(Icons.location_on_outlined, size: 16, color: SiteColors.stone),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        address,
                        style: const TextStyle(fontSize: 13, height: 1.3, color: SiteColors.ink),
                      ),
                    ),
                  ],
                ),
              ],
              const SizedBox(height: 8),
              Text(
                'Leg distance: +$legDist mi · Est. drive $legDur mins',
                style: const TextStyle(fontSize: 12, color: SiteColors.stone, fontWeight: FontWeight.w600),
              ),
              const SizedBox(height: 18),
              Row(
                children: [
                  if (navUrl.isNotEmpty)
                    Expanded(
                      child: ElevatedButton.icon(
                        style: ElevatedButton.styleFrom(
                          backgroundColor: const Color(0xFF9333EA),
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.symmetric(vertical: 12),
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                        ),
                        onPressed: () {
                          Navigator.of(ctx).pop();
                          widget.onNavigate(navUrl);
                        },
                        icon: const Icon(Icons.navigation, size: 16),
                        label: const Text('Navigate', style: TextStyle(fontWeight: FontWeight.w800)),
                      ),
                    ),
                  if (navUrl.isNotEmpty && jobId.isNotEmpty) const SizedBox(width: 10),
                  if (jobId.isNotEmpty)
                    Expanded(
                      child: OutlinedButton.icon(
                        style: OutlinedButton.styleFrom(
                          padding: const EdgeInsets.symmetric(vertical: 12),
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                        ),
                        onPressed: () {
                          Navigator.of(ctx).pop();
                          Navigator.of(context).push(
                            MaterialPageRoute(
                              builder: (_) => JobDetailScreen(api: widget.api, jobId: jobId),
                            ),
                          );
                        },
                        icon: const Icon(Icons.description_outlined, size: 16),
                        label: const Text('Job Details', style: TextStyle(fontWeight: FontWeight.w800)),
                      ),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final start = widget.plan['start'] as Map<String, dynamic>?;
    final startLat = (start?['lat'] as num?)?.toDouble();
    final startLng = (start?['lng'] as num?)?.toDouble();
    final stops = (widget.plan['stops'] as List<dynamic>?) ?? [];
    final routePoints = _getRoutePoints();

    final markers = <Marker>[];
    if (startLat != null && startLng != null) {
      markers.add(
        Marker(
          point: LatLng(startLat, startLng),
          width: 38,
          height: 38,
          child: GestureDetector(
            onTap: () {
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(
                  content: Text('Start Origin: ${start?['address'] ?? 'Start Depot'}'),
                  backgroundColor: const Color(0xFF065F46),
                  duration: const Duration(seconds: 3),
                ),
              );
            },
            child: Container(
              decoration: BoxDecoration(
                color: SiteColors.green,
                shape: BoxShape.circle,
                border: Border.all(color: Colors.white, width: 2.5),
                boxShadow: const [
                  BoxShadow(color: Colors.black26, blurRadius: 6, offset: Offset(0, 2)),
                ],
              ),
              child: const Icon(Icons.play_arrow, color: Colors.white, size: 22),
            ),
          ),
        ),
      );
    }

    for (final raw in stops) {
      if (raw is Map) {
        final lat = (raw['lat'] as num?)?.toDouble();
        final lng = (raw['lng'] as num?)?.toDouble();
        if (lat == null || lng == null) continue;
        final stopNum = raw['stop_number'] ?? '';
        markers.add(
          Marker(
            point: LatLng(lat, lng),
            width: 36,
            height: 36,
            child: GestureDetector(
              onTap: () => _showStopSheet(context, Map<String, dynamic>.from(raw)),
              child: Container(
                decoration: BoxDecoration(
                  gradient: const LinearGradient(
                    colors: [Color(0xFFA855F7), Color(0xFF7E22CE)],
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                  ),
                  shape: BoxShape.circle,
                  border: Border.all(color: Colors.white, width: 2.5),
                  boxShadow: const [
                    BoxShadow(color: Colors.black26, blurRadius: 6, offset: Offset(0, 2)),
                  ],
                ),
                alignment: Alignment.center,
                child: Text(
                  '$stopNum',
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.w900,
                    fontSize: 14,
                  ),
                ),
              ),
            ),
          ),
        );
      }
    }

    CameraFit? initialFit;
    LatLng centerPoint = const LatLng(51.5074, -0.1278);
    if (routePoints.isNotEmpty) {
      centerPoint = routePoints.first;
      if (routePoints.length > 1) {
        initialFit = CameraFit.bounds(
          bounds: LatLngBounds.fromPoints(routePoints),
          padding: const EdgeInsets.all(36),
        );
      }
    }

    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: SiteColors.line),
        boxShadow: const [
          BoxShadow(color: Color(0x0A000000), blurRadius: 8, offset: Offset(0, 2)),
        ],
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Dark Header
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            color: const Color(0xFF111827),
            child: Row(
              children: [
                const Icon(Icons.map_outlined, color: Color(0xFFC084FC), size: 18),
                const SizedBox(width: 8),
                const Text(
                  'ROUTE MAP VIEW',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 11,
                    fontWeight: FontWeight.w900,
                    letterSpacing: 1.1,
                  ),
                ),
                if (widget.plan['is_road_calculated'] == true) ...[
                  const SizedBox(width: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(
                      color: const Color(0xFF9333EA).withValues(alpha: 0.35),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: const Color(0xFFA855F7), width: 0.8),
                    ),
                    child: const Text(
                      'ROAD-WISE',
                      style: TextStyle(color: Color(0xFFE9D5FF), fontSize: 9, fontWeight: FontWeight.w800, letterSpacing: 0.5),
                    ),
                  ),
                ],
                const Spacer(),
                if (routePoints.isNotEmpty)
                  InkWell(
                    onTap: () => _fitAll(routePoints),
                    borderRadius: BorderRadius.circular(8),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: const [
                          Icon(Icons.zoom_out_map, color: Color(0xFFE9D5FF), size: 14),
                          SizedBox(width: 4),
                          Text(
                            'Fit All',
                            style: TextStyle(
                              color: Color(0xFFE9D5FF),
                              fontSize: 11,
                              fontWeight: FontWeight.w800,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          ),

          // Interactive Map
          SizedBox(
            height: 320,
            child: FlutterMap(
              mapController: _mapController,
              options: MapOptions(
                initialCenter: centerPoint,
                initialZoom: 13.0,
                initialCameraFit: initialFit,
              ),
              children: [
                TileLayer(
                  urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                  userAgentPackageName: 'com.sitelog.demo',
                ),
                if (routePoints.length > 1)
                  PolylineLayer(
                    polylines: [
                      Polyline(
                        points: routePoints,
                        color: const Color(0xFF9333EA),
                        strokeWidth: 4.0,
                      ),
                    ],
                  ),
                MarkerLayer(markers: markers),
              ],
            ),
          ),

          // Map Legend / Footer
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: const BoxDecoration(
              color: Color(0xFFF9FAFB),
              border: Border(top: BorderSide(color: SiteColors.line)),
            ),
            child: Row(
              children: [
                Container(width: 8, height: 8, decoration: const BoxDecoration(color: SiteColors.green, shape: BoxShape.circle)),
                const SizedBox(width: 5),
                const Text('Start', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: SiteColors.stone)),
                const SizedBox(width: 12),
                Container(width: 8, height: 8, decoration: const BoxDecoration(color: Color(0xFF9333EA), shape: BoxShape.circle)),
                const SizedBox(width: 5),
                const Text('Stops', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: SiteColors.stone)),
                const SizedBox(width: 12),
                Container(width: 14, height: 3, decoration: BoxDecoration(color: const Color(0xFF9333EA), borderRadius: BorderRadius.circular(2))),
                const SizedBox(width: 5),
                const Text('Route', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: SiteColors.stone)),
                const Spacer(),
                const Text('© OpenStreetMap', style: TextStyle(fontSize: 10, color: Color(0xFF9CA3AF))),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _RouteStopCard extends StatelessWidget {
  const _RouteStopCard({
    required this.stop,
    required this.api,
    required this.onNavigate,
  });

  final Map<String, dynamic> stop;
  final Api api;
  final VoidCallback onNavigate;

  @override
  Widget build(BuildContext context) {
    final stopNum = stop['stop_number'] ?? 1;
    final workRef = stop['work_ref']?.toString() ?? '';
    final address = stop['site_address']?.toString() ?? '';
    final client = stop['client']?.toString() ?? '';
    final legDist = stop['leg_distance_miles'] ?? 0.0;
    final legMins = stop['leg_duration_mins'] ?? 0;
    final cumDist = stop['cumulative_distance_miles'] ?? 0.0;
    final status = stop['status']?.toString();
    final statusLabel = stop['status_label']?.toString();

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: SiteColors.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 32,
                height: 32,
                decoration: BoxDecoration(
                  gradient: const LinearGradient(
                    colors: [Color(0xFFA855F7), Color(0xFF7E22CE)],
                  ),
                  borderRadius: BorderRadius.circular(10),
                ),
                alignment: Alignment.center,
                child: Text('$stopNum', style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w900)),
              ),
              const SizedBox(width: 10),
              Text(workRef, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: SiteColors.ink)),
              const SizedBox(width: 6),
              StatusPill(status: status, label: statusLabel),
              const Spacer(),
              Text('+$legDist mi', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w800, color: SiteColors.indigo)),
            ],
          ),
          if (client.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(client, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: SiteColors.stone)),
          ],
          const SizedBox(height: 6),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Icon(Icons.location_on, size: 15, color: SiteColors.indigo),
              const SizedBox(width: 4),
              Expanded(
                child: Text(
                  address,
                  style: const TextStyle(fontSize: 13, color: SiteColors.ink, height: 1.3),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: SiteColors.paper,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('Drive: $legMins mins from previous stop', style: const TextStyle(fontSize: 11, color: SiteColors.stone, fontWeight: FontWeight.w600)),
                Text('Total: $cumDist mi', style: const TextStyle(fontSize: 11, color: SiteColors.stone)),
              ],
            ),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              FilledButton.icon(
                style: FilledButton.styleFrom(
                  backgroundColor: SiteColors.indigo,
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  visualDensity: VisualDensity.compact,
                ),
                onPressed: onNavigate,
                icon: const Icon(Icons.navigation, size: 14),
                label: const Text('Navigate', style: TextStyle(fontSize: 12)),
              ),
              const SizedBox(width: 8),
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                  visualDensity: VisualDensity.compact,
                ),
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute(builder: (_) => ChatScreen(api: api, job: stop)),
                ),
                icon: const Icon(Icons.chat_bubble_outline, size: 14),
                label: const Text('Chat', style: TextStyle(fontSize: 12)),
              ),
              const Spacer(),
              TextButton(
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute(builder: (_) => JobDetailScreen(api: api, jobId: stop['job_id'] as String)),
                ),
                child: const Text('Details', style: TextStyle(fontSize: 12)),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _SectionTile extends StatelessWidget {
  const _SectionTile({required this.status, required this.title, required this.blurb, required this.count, required this.onTap});

  final String status;
  final String title;
  final String blurb;
  final Object count;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = SiteColors.status(status);
    return Material(
      color: Colors.white,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20), side: const BorderSide(color: SiteColors.line)),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(width: 6, color: color),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(12, 12, 12, 12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title.toUpperCase(), style: TextStyle(color: color, fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.4)),
                    const Spacer(),
                    Text('$count', style: const TextStyle(fontSize: 32, fontWeight: FontWeight.w900, height: 1, color: SiteColors.ink)),
                    const SizedBox(height: 4),
                    Text(blurb, style: const TextStyle(fontSize: 12, color: SiteColors.stone)),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class SectionScreen extends StatefulWidget {
  const SectionScreen({super.key, required this.api, required this.status, required this.title});

  final Api api;
  final String status;
  final String title;

  @override
  State<SectionScreen> createState() => _SectionScreenState();
}

class _SectionScreenState extends State<SectionScreen> {
  late Future<Map<String, dynamic>> _data;

  @override
  void initState() {
    super.initState();
    _data = widget.api.jobs(status: widget.status);
  }

  Future<void> _reload() async {
    setState(() => _data = widget.api.jobs(status: widget.status));
    await _data;
  }

  @override
  Widget build(BuildContext context) {
    final color = SiteColors.status(widget.status);
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: RefreshIndicator(
        onRefresh: _reload,
        child: FutureBuilder<Map<String, dynamic>>(
          future: _data,
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              return ListView(children: const [Padding(padding: EdgeInsets.all(24), child: Text('Could not load jobs.'))]);
            }
            if (!snapshot.hasData) return const Center(child: CircularProgressIndicator());
            final jobs = snapshot.data!['jobs'] as List<dynamic>;
            if (jobs.isEmpty) {
              return ListView(children: const [Padding(padding: EdgeInsets.all(24), child: Text('Nothing in this section.'))]);
            }
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text('${jobs.length} ON THIS BOARD', style: TextStyle(color: color, fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.8)),
                const SizedBox(height: 12),
                for (final raw in jobs) ...[
                  JobCard(
                    job: raw as Map<String, dynamic>,
                    onTap: () async {
                      await Navigator.of(context).push(MaterialPageRoute(builder: (_) => JobDetailScreen(api: widget.api, jobId: raw['id'] as String)));
                      await _reload();
                    },
                  ),
                  const SizedBox(height: 12),
                ],
              ],
            );
          },
        ),
      ),
    );
  }
}

class JobDetailScreen extends StatefulWidget {
  const JobDetailScreen({super.key, required this.api, required this.jobId});

  final Api api;
  final String jobId;

  @override
  State<JobDetailScreen> createState() => _JobDetailScreenState();
}

class _JobDetailScreenState extends State<JobDetailScreen> {
  late Future<Map<String, dynamic>> _job;

  @override
  void initState() {
    super.initState();
    _job = widget.api.job(widget.jobId);
  }

  Future<void> _reload() async {
    setState(() => _job = widget.api.job(widget.jobId));
    await _job;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Site report')),
      body: FutureBuilder<Map<String, dynamic>>(
        future: _job,
        builder: (context, snapshot) {
          if (snapshot.hasError) return const Center(child: Text('Could not load this job.'));
          if (!snapshot.hasData) return const Center(child: CircularProgressIndicator());
          final job = snapshot.data!;
          final photos = job['photos'] as List<dynamic>? ?? [];
          final color = SiteColors.status(job['status']?.toString());
          final rows = [
            ('Arrival', job['arrival_time']),
            ('Departure', job['departure_time']),
            ('Issue', job['issue']),
            ('Findings', job['findings']),
            ('Materials', job['materials']),
            ('Part number', job['part_number']),
            ('Recommended works', job['recommended_works']),
          ];
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Container(
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(24),
                  border: Border.all(color: SiteColors.line),
                ),
                clipBehavior: Clip.antiAlias,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Container(height: 6, color: color),
                    Padding(
                      padding: const EdgeInsets.fromLTRB(18, 16, 18, 8),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('SITE REPORT', style: TextStyle(color: color, fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 1.2)),
                          const SizedBox(height: 4),
                          Text(job['work_ref']?.toString() ?? '', style: const TextStyle(fontSize: 32, fontWeight: FontWeight.w900, height: 1.05, color: SiteColors.ink)),
                          const SizedBox(height: 4),
                          Text(
                            '${(job['client'] ?? '').toString().isEmpty ? 'No client named' : job['client']}${(job['job_date'] ?? '').toString().isEmpty ? '' : ' · ${job['job_date']}'}',
                            style: const TextStyle(color: SiteColors.stone),
                          ),
                          const SizedBox(height: 12),
                          Text(job['site_address']?.toString() ?? '', style: const TextStyle(fontSize: 16, height: 1.3)),
                          const SizedBox(height: 12),
                          Wrap(
                            spacing: 8,
                            runSpacing: 8,
                            children: [
                              StatusPill(status: job['status']?.toString(), label: job['status_label']?.toString()),
                              _Chip((job['source'] ?? '').toString().toUpperCase()),
                              if ((job['engineers'] ?? '').toString().isNotEmpty) _Chip(job['engineers'].toString()),
                            ],
                          ),
                          const SizedBox(height: 14),
                          Wrap(
                            spacing: 8,
                            runSpacing: 8,
                            children: [
                              FilledButton.icon(
                                onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => ChatScreen(api: widget.api, job: job))),
                                icon: const Icon(Icons.chat_bubble_outline, size: 16),
                                label: const Text('Chat'),
                              ),
                              if ((job['site_address'] ?? '').toString().isNotEmpty)
                                FilledButton.tonalIcon(
                                  onPressed: () {
                                    final addr = Uri.encodeComponent(job['site_address'].toString());
                                    final url = Uri.parse('https://www.google.com/maps/search/?api=1&query=$addr');
                                    launchUrl(url, mode: LaunchMode.externalApplication);
                                  },
                                  icon: const Icon(Icons.navigation_outlined, size: 16),
                                  label: const Text('Navigate'),
                                ),
                              if (job['status'] != 'discarded')
                                OutlinedButton(
                                  onPressed: () async {
                                    final saved = await Navigator.of(context).push<bool>(
                                      MaterialPageRoute(builder: (_) => DiscardScreen(api: widget.api, jobId: widget.jobId, workRef: job['work_ref']?.toString() ?? '')),
                                    );
                                    if (saved == true) await _reload();
                                  },
                                  style: OutlinedButton.styleFrom(foregroundColor: SiteColors.rose, side: const BorderSide(color: Color(0xFFFECDD3))),
                                  child: const Text('Discard'),
                                ),
                            ],
                          ),
                        ],
                      ),
                    ),
                    if (job['status'] == 'discarded')
                      Container(
                        color: SiteColors.washRose,
                        padding: const EdgeInsets.fromLTRB(18, 14, 18, 14),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('Discarded', style: TextStyle(color: SiteColors.rose, fontWeight: FontWeight.w800)),
                            const SizedBox(height: 4),
                            Text(job['discard_reason']?.toString() ?? '', style: const TextStyle(color: SiteColors.rose)),
                            const SizedBox(height: 8),
                            FilledButton(
                              onPressed: () async {
                                await widget.api.restore(widget.jobId);
                                await _reload();
                              },
                              child: const Text('Restore'),
                            ),
                          ],
                        ),
                      ),
                    const Divider(height: 1, color: SiteColors.line),
                    Padding(
                      padding: const EdgeInsets.fromLTRB(18, 4, 18, 8),
                      child: Column(
                        children: [
                          for (final row in rows) _ReportLine(label: row.$1, value: (row.$2 ?? '').toString()),
                        ],
                      ),
                    ),
                    if (job['source'] == 'email') ...[
                      const Divider(height: 1, color: SiteColors.line),
                      Padding(
                        padding: const EdgeInsets.all(18),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('EMAIL', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.8, color: Color(0xFFA8A29E))),
                            const SizedBox(height: 8),
                            Container(
                              width: double.infinity,
                              padding: const EdgeInsets.all(12),
                              decoration: BoxDecoration(color: SiteColors.paper, borderRadius: BorderRadius.circular(16)),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text('From  ${job['email_from'] ?? ''}'),
                                  Text('Subject  ${job['email_subject'] ?? ''}'),
                                  const SizedBox(height: 8),
                                  Container(
                                    decoration: const BoxDecoration(border: Border(left: BorderSide(color: Color(0xFFD6D3D1), width: 2))),
                                    padding: const EdgeInsets.only(left: 10),
                                    child: Text(job['email_body']?.toString() ?? ''),
                                  ),
                                ],
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                    if (photos.isNotEmpty) ...[
                      const Divider(height: 1, color: SiteColors.line),
                      Padding(
                        padding: const EdgeInsets.all(18),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('PHOTOS', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.8, color: Color(0xFFA8A29E))),
                            const SizedBox(height: 8),
                            for (final photo in photos)
                              Padding(
                                padding: const EdgeInsets.only(bottom: 12),
                                child: ClipRRect(
                                  borderRadius: BorderRadius.circular(16),
                                  child: Image.network(
                                    '${widget.api.baseUrl}${photo['url']}',
                                    headers: {'Authorization': 'Bearer ${widget.api.token}'},
                                  ),
                                ),
                              ),
                          ],
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip(this.label);

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: const Color(0xFFF5F5F4), borderRadius: BorderRadius.circular(999)),
      child: Text(label, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: Color(0xFF44403C))),
    );
  }
}

class _ReportLine extends StatelessWidget {
  const _ReportLine({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(vertical: 12),
      decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: Color(0xFFF5F5F4)))),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label.toUpperCase(), style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.7, color: Color(0xFFA8A29E))),
          const SizedBox(height: 4),
          Text(value.isEmpty ? '—' : value, style: const TextStyle(fontSize: 15, height: 1.4)),
        ],
      ),
    );
  }
}

class DiscardScreen extends StatefulWidget {
  const DiscardScreen({super.key, required this.api, required this.jobId, required this.workRef});

  final Api api;
  final String jobId;
  final String workRef;

  @override
  State<DiscardScreen> createState() => _DiscardScreenState();
}

class _DiscardScreenState extends State<DiscardScreen> {
  final _reason = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('Discard ${widget.workRef}')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text('WRITTEN OFF', style: TextStyle(color: SiteColors.rose, fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 1.1)),
          const SizedBox(height: 6),
          const Text('Write why this job is being discarded. You can restore it later.', style: TextStyle(color: SiteColors.stone)),
          const SizedBox(height: 16),
          TextField(controller: _reason, maxLines: 4, decoration: const InputDecoration(labelText: 'Reason')),
          const SizedBox(height: 16),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: SiteColors.rose),
            onPressed: _busy
                ? null
                : () async {
                    if (_reason.text.trim().isEmpty) return;
                    setState(() => _busy = true);
                    await widget.api.discard(widget.jobId, _reason.text.trim());
                    if (context.mounted) Navigator.of(context).pop(true);
                  },
            child: const Text('Discard job'),
          ),
        ],
      ),
    );
  }
}

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key, required this.api, required this.job});

  final Api api;
  final Map<String, dynamic> job;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _body = TextEditingController();
  late Future<List<dynamic>> _messages;
  bool _sending = false;
  Timer? _pollTimer;

  @override
  void initState() {
    super.initState();
    _messages = widget.api.messages(widget.job['id'].toString());
    _pollTimer = Timer.periodic(const Duration(seconds: 4), (_) {
      if (mounted) {
        setState(() => _messages = widget.api.messages(widget.job['id'].toString()));
      }
    });
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    _body.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final text = _body.text.trim();
    if (text.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      await widget.api.sendMessage(widget.job['id'] as String, text);
      _body.clear();
      setState(() => _messages = widget.api.messages(widget.job['id'] as String));
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final mineId = widget.api.user['id'];
    return Scaffold(
      backgroundColor: SiteColors.chat,
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Chat · ${widget.job['work_ref']}', style: const TextStyle(fontSize: 16)),
            Text(widget.job['site_address']?.toString() ?? '', style: const TextStyle(fontSize: 12, color: Color(0xFFD6D3D1), fontWeight: FontWeight.w400)),
          ],
        ),
        actions: [
          IconButton(
            tooltip: 'Job Details',
            icon: const Icon(Icons.description_outlined),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(
                builder: (_) => JobDetailScreen(
                  api: widget.api,
                  jobId: widget.job['id'] as String,
                ),
              ),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: FutureBuilder<List<dynamic>>(
              future: _messages,
              builder: (context, snapshot) {
                if (snapshot.hasError) return const Center(child: Text('Could not load messages.'));
                if (!snapshot.hasData) return const Center(child: CircularProgressIndicator());
                final messages = snapshot.data!;
                if (messages.isEmpty) return const Center(child: Text('No messages yet.', style: TextStyle(color: SiteColors.stone)));
                return ListView(
                  padding: const EdgeInsets.fromLTRB(12, 16, 12, 16),
                  children: [
                    for (final raw in messages)
                      _Bubble(message: raw as Map<String, dynamic>, mine: raw['author_id'] == mineId),
                  ],
                );
              },
            ),
          ),
          Material(
            color: Colors.white,
            child: SafeArea(
              top: false,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(12, 8, 4, 8),
                child: Row(
                  children: [
                    Expanded(child: TextField(controller: _body, decoration: const InputDecoration(hintText: 'Write a message'))),
                    IconButton(onPressed: _sending ? null : _send, icon: const Icon(Icons.send, color: SiteColors.ink)),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.message, required this.mine});

  final Map<String, dynamic> message;
  final bool mine;

  @override
  Widget build(BuildContext context) {
    final name = message['author_name']?.toString() ?? '';
    final role = message['author_role'] == 'office' ? ' · Office' : '';
    final when = _shortWhen(message['created_at']?.toString());
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Align(
        alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
        child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * 0.78),
          child: Column(
            crossAxisAlignment: mine ? CrossAxisAlignment.end : CrossAxisAlignment.start,
            children: [
              if (!mine)
                Padding(
                  padding: const EdgeInsets.only(left: 4, bottom: 3),
                  child: Text('$name$role', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: SiteColors.stone)),
                ),
              DecoratedBox(
                decoration: BoxDecoration(
                  color: mine ? SiteColors.ink : Colors.white,
                  borderRadius: BorderRadius.only(
                    topLeft: const Radius.circular(18),
                    topRight: const Radius.circular(18),
                    bottomLeft: Radius.circular(mine ? 18 : 5),
                    bottomRight: Radius.circular(mine ? 5 : 18),
                  ),
                  boxShadow: mine ? null : const [BoxShadow(color: Color(0x14000000), blurRadius: 6, offset: Offset(0, 1))],
                ),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  child: Text(message['body']?.toString() ?? '', style: TextStyle(color: mine ? Colors.white : SiteColors.ink, height: 1.35)),
                ),
              ),
              if (when.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 3, left: 4, right: 4),
                  child: Text(when, style: const TextStyle(fontSize: 11, color: SiteColors.stone)),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

DateTime? _parseDate(String raw) {
  final parts = raw.split('/');
  if (parts.length != 3) return null;
  final day = int.tryParse(parts[0]);
  final month = int.tryParse(parts[1]);
  final year = int.tryParse(parts[2]);
  if (day == null || month == null || year == null) return null;
  return DateTime(year, month, day);
}

String _formatDate(DateTime date) {
  final day = date.day.toString().padLeft(2, '0');
  final month = date.month.toString().padLeft(2, '0');
  return '$day/$month/${date.year}';
}

TimeOfDay? _parseTime(String raw) {
  final parts = raw.split(':');
  if (parts.length < 2) return null;
  final hour = int.tryParse(parts[0]);
  final minute = int.tryParse(parts[1]);
  if (hour == null || minute == null) return null;
  return TimeOfDay(hour: hour, minute: minute);
}

String _formatTime(TimeOfDay time) {
  final hour = time.hour.toString().padLeft(2, '0');
  final minute = time.minute.toString().padLeft(2, '0');
  return '$hour:$minute';
}

String _shortWhen(String? raw) {
  if (raw == null || raw.length < 16) return raw ?? '';
  return '${raw.substring(0, 10)} ${raw.substring(11, 16)}';
}

class NewJobScreen extends StatefulWidget {
  const NewJobScreen({super.key, required this.api});

  final Api api;

  @override
  State<NewJobScreen> createState() => _NewJobScreenState();
}

class _NewJobScreenState extends State<NewJobScreen> {
  final _ref = TextEditingController();
  final _date = TextEditingController();
  final _client = TextEditingController();
  final _address = TextEditingController();
  final _arrival = TextEditingController();
  final _departure = TextEditingController();
  final _issue = TextEditingController();
  final _findings = TextEditingController();
  final _materials = TextEditingController();
  final _part = TextEditingController();
  final _recommended = TextEditingController();
  final Set<String> _assignees = {};
  bool _complete = false;
  bool _busy = false;
  final List<XFile> _photos = [];
  late Future<List<dynamic>> _engineers;

  @override
  void initState() {
    super.initState();
    _engineers = widget.api.engineers();
  }

  @override
  void dispose() {
    for (final controller in [_ref, _date, _client, _address, _arrival, _departure, _issue, _findings, _materials, _part, _recommended]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _pickTime(TextEditingController controller) async {
    final current = _parseTime(controller.text) ?? TimeOfDay.now();
    final picked = await showTimePicker(context: context, initialTime: current);
    if (picked != null) setState(() => controller.text = _formatTime(picked));
  }

  Widget _pickerField({
    required TextEditingController controller,
    required String label,
    required String hint,
    required IconData icon,
    required VoidCallback onPick,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextField(
        controller: controller,
        readOnly: true,
        onTap: onPick,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          suffixIcon: IconButton(onPressed: onPick, icon: Icon(icon)),
        ),
      ),
    );
  }

  Future<void> _save() async {
    if (_ref.text.trim().isEmpty || _address.text.trim().isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Work ref and address are required.')));
      return;
    }
    setState(() => _busy = true);
    try {
      final job = await widget.api.createJob({
        'work_ref': _ref.text.trim(),
        'job_date': _date.text.trim(),
        'client': _client.text.trim(),
        'site_address': _address.text.trim(),
        'arrival_time': _arrival.text.trim(),
        'departure_time': _departure.text.trim(),
        'issue': _issue.text.trim(),
        'findings': _findings.text.trim(),
        'materials': _materials.text.trim(),
        'part_number': _part.text.trim(),
        'recommended_works': _recommended.text.trim(),
        'is_complete': _complete,
        'engineer_ids': _assignees.toList(),
      });
      for (final photo in _photos) {
        await widget.api.uploadPhoto(job['id'] as String, File(photo.path));
      }
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (_) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Could not save the job.')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    Widget field(TextEditingController controller, String label, {int lines = 1}) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextField(controller: controller, maxLines: lines, decoration: InputDecoration(labelText: label)),
      );
    }

    return Scaffold(
      appBar: AppBar(title: const Text('New job')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
        children: [
          const Text('JOB', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.8, color: Color(0xFFA8A29E))),
          const SizedBox(height: 8),
          field(_ref, 'Work ref'),
          _pickerField(
            controller: _date,
            label: 'Visit date',
            hint: 'Choose the day of the visit',
            icon: Icons.calendar_today_outlined,
            onPick: () async {
              final now = DateTime.now();
              final current = _parseDate(_date.text) ?? now;
              final picked = await showDatePicker(
                context: context,
                initialDate: current,
                firstDate: DateTime(now.year - 5),
                lastDate: DateTime(now.year + 2),
              );
              if (picked != null) setState(() => _date.text = _formatDate(picked));
            },
          ),
          field(_client, 'Client'),
          field(_address, 'Site address'),
          const Text('VISIT', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.8, color: Color(0xFFA8A29E))),
          const SizedBox(height: 8),
          _pickerField(
            controller: _arrival,
            label: 'Arrival',
            hint: 'When you arrived on site',
            icon: Icons.schedule,
            onPick: () => _pickTime(_arrival),
          ),
          _pickerField(
            controller: _departure,
            label: 'Departure',
            hint: 'When you left the site',
            icon: Icons.schedule,
            onPick: () => _pickTime(_departure),
          ),
          const Text('REPORT', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.8, color: Color(0xFFA8A29E))),
          const SizedBox(height: 8),
          field(_issue, 'Issue', lines: 3),
          field(_findings, 'Findings', lines: 3),
          field(_materials, 'Materials'),
          field(_part, 'Part number'),
          field(_recommended, 'Recommended works', lines: 2),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Job complete'),
            value: _complete,
            onChanged: (value) => setState(() => _complete = value),
          ),
          const Text('ASSIGNED ENGINEERS', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.8, color: Color(0xFFA8A29E))),
          FutureBuilder<List<dynamic>>(
            future: _engineers,
            builder: (context, snapshot) {
              final people = snapshot.data ?? [];
              return Column(
                children: [
                  for (final raw in people)
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text((raw as Map<String, dynamic>)['name']?.toString() ?? ''),
                      value: _assignees.contains(raw['id']),
                      onChanged: (checked) {
                        setState(() {
                          if (checked == true) {
                            _assignees.add(raw['id'] as String);
                          } else {
                            _assignees.remove(raw['id']);
                          }
                        });
                      },
                    ),
                ],
              );
            },
          ),
          Row(
            children: [
              TextButton.icon(
                onPressed: () async {
                  final file = await ImagePicker().pickImage(source: ImageSource.camera, imageQuality: 80);
                  if (file != null) setState(() => _photos.add(file));
                },
                icon: const Icon(Icons.photo_camera),
                label: const Text('Camera'),
              ),
              TextButton.icon(
                onPressed: () async {
                  final file = await ImagePicker().pickImage(source: ImageSource.gallery, imageQuality: 80);
                  if (file != null) setState(() => _photos.add(file));
                },
                icon: const Icon(Icons.photo),
                label: const Text('Gallery'),
              ),
            ],
          ),
          Text('${_photos.length} photo(s) attached', style: const TextStyle(color: SiteColors.stone)),
          const SizedBox(height: 16),
          FilledButton(onPressed: _busy ? null : _save, child: Text(_busy ? 'Saving...' : 'Save job')),
        ],
      ),
    );
  }
}

class MailboxScreen extends StatefulWidget {
  const MailboxScreen({super.key, required this.api});

  final Api api;

  @override
  State<MailboxScreen> createState() => _MailboxScreenState();
}

class _MailboxScreenState extends State<MailboxScreen> {
  final _host = TextEditingController();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _port = TextEditingController(text: '993');
  bool _ready = false;
  bool _busy = false;
  bool _saved = false;

  @override
  void initState() {
    super.initState();
    widget.api.mailbox().then((mailbox) {
      _host.text = mailbox['host']?.toString() ?? '';
      _email.text = mailbox['email']?.toString() ?? '';
      _port.text = '${mailbox['port'] ?? 993}';
      if (mounted) setState(() => _ready = true);
    });
  }

  @override
  void dispose() {
    _host.dispose();
    _email.dispose();
    _password.dispose();
    _port.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Mailbox')),
      body: _ready
          ? ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Text('New unread mail becomes a New Job. Leave the password blank to keep the saved one.', style: TextStyle(color: SiteColors.stone)),
                if (_saved) ...[
                  const SizedBox(height: 12),
                  DecoratedBox(
                    decoration: BoxDecoration(
                      color: Color(0x24059669),
                      borderRadius: BorderRadius.all(Radius.circular(12)),
                      border: Border.fromBorderSide(BorderSide(color: Color(0x33059669))),
                    ),
                    child: Padding(
                      padding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                      child: Text(
                        'Saved. The server checks the inbox about once a minute.',
                        style: TextStyle(color: SiteColors.green, fontWeight: FontWeight.w600),
                      ),
                    ),
                  ),
                ],
                const SizedBox(height: 16),
                TextField(controller: _host, decoration: const InputDecoration(labelText: 'IMAP host')),
                const SizedBox(height: 12),
                TextField(controller: _port, decoration: const InputDecoration(labelText: 'Port')),
                const SizedBox(height: 12),
                TextField(controller: _email, decoration: const InputDecoration(labelText: 'Email')),
                const SizedBox(height: 12),
                TextField(controller: _password, obscureText: true, decoration: const InputDecoration(labelText: 'App password')),
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: _busy
                      ? null
                      : () async {
                          setState(() => _busy = true);
                          await widget.api.saveMailbox(_host.text.trim(), _email.text.trim(), _password.text, int.tryParse(_port.text) ?? 993);
                          if (context.mounted) {
                            setState(() {
                              _busy = false;
                              _saved = true;
                            });
                          }
                        },
                  child: const Text('Save mailbox'),
                ),
              ],
            )
          : const Center(child: CircularProgressIndicator()),
    );
  }
}
