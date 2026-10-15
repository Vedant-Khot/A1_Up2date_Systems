import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:sitelog/main.dart';

void main() {
  setUpAll(() {
    GoogleFonts.config.allowRuntimeFetching = false;
  });

  testWidgets('login screen shows default server 187.127.158.102 and demo engineer', (WidgetTester tester) async {
    await tester.pumpWidget(const SiteLogApp());
    expect(find.text('SiteLog'), findsOneWidget);
    expect(find.text('187.127.158.102'), findsOneWidget);
    expect(find.text('alex@sitelog.demo'), findsOneWidget);
  });

  test('Api.normalizeBaseUrl handles various formats correctly', () {
    expect(Api.normalizeBaseUrl(''), 'http://187.127.158.102:8010');
    expect(Api.normalizeBaseUrl('187.127.158.102'), 'http://187.127.158.102:8010');
    expect(Api.normalizeBaseUrl('http://187.127.158.102'), 'http://187.127.158.102:8010');
    expect(Api.normalizeBaseUrl('http://187.127.158.102:8010'), 'http://187.127.158.102:8010');
    expect(Api.normalizeBaseUrl('187.127.158.102:8010/'), 'http://187.127.158.102:8010');
    expect(Api.normalizeBaseUrl('http://127.0.0.1:8000/'), 'http://127.0.0.1:8000');
  });

  testWidgets('app drawer contains chats, job board, and all categories', (WidgetTester tester) async {
    final fakeApi = Api('http://127.0.0.1:8000', 'fake_token', {
      'id': 'u1',
      'name': 'Alex River',
      'email': 'alex@sitelog.demo',
      'role': 'engineer',
    });

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          drawer: AppDrawer(api: fakeApi, currentRoute: 'chats'),
          body: const Text('Body'),
        ),
      ),
    );

    final scaffoldState = tester.firstState<ScaffoldState>(find.byType(Scaffold));
    scaffoldState.openDrawer();
    await tester.pumpAndSettle();

    expect(find.text('Chats'), findsOneWidget);
    expect(find.text('Job Board'), findsOneWidget);
    expect(find.text('Route Planner'), findsOneWidget);
    expect(find.text('CATEGORIES'), findsOneWidget);
    expect(find.text('New jobs'), findsOneWidget);
    expect(find.text('In progress jobs'), findsOneWidget);
    expect(find.text('Discarded jobs'), findsOneWidget);
    expect(find.text('Completed jobs'), findsOneWidget);
    expect(find.text('Sign out'), findsOneWidget);
  });

  testWidgets('HomeScreen is chat first with Chats title and drawer', (WidgetTester tester) async {
    final fakeApi = Api('http://127.0.0.1:8000', 'fake_token', {
      'id': 'u1',
      'name': 'Alex River',
      'email': 'alex@sitelog.demo',
      'role': 'engineer',
    });

    await tester.pumpWidget(
      MaterialApp(
        home: HomeScreen(api: fakeApi),
      ),
    );

    expect(find.text('Chats'), findsOneWidget);
    expect(find.byType(FloatingActionButton), findsOneWidget);
    expect(find.byIcon(Icons.menu), findsOneWidget);
  });

  testWidgets('RoutePlannerScreen displays route map header and controls', (WidgetTester tester) async {
    final fakeApi = Api('http://127.0.0.1:8000', 'fake_token', {
      'id': 'u1',
      'name': 'Alex River',
      'email': 'alex@sitelog.demo',
      'role': 'engineer',
    });

    await tester.pumpWidget(
      MaterialApp(
        home: RoutePlannerScreen(api: fakeApi),
      ),
    );

    expect(find.text('Route Planner'), findsOneWidget);
    expect(find.byIcon(Icons.refresh), findsOneWidget);
  });
}
